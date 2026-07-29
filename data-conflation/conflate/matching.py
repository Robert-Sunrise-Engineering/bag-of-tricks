"""Matching module for conflation operations.

Provides functions for finding and ranking candidate features for conflation
based on spatial proximity and type matching.
"""

import numpy as np
from scipy.optimize import linear_sum_assignment

from conflate.geometry import geodesic_distance


def pick_closest(candidates: list[dict], captured_lon: float, captured_lat: float) -> tuple:
    """Find the closest candidate to a captured feature and return all ranked by distance.

    Args:
        candidates: List of candidate feature dicts, each with "lon" and "lat" keys.
        captured_lon: Longitude of the captured feature.
        captured_lat: Latitude of the captured feature.

    Returns:
        A 3-tuple: (closest_candidate, closest_distance, all_candidates_with_distances_sorted)
        where all_candidates_with_distances_sorted is a list of (candidate_dict, distance)
        tuples sorted ascending by distance. If candidates is empty, returns (None, None, []).
    """
    if not candidates:
        return (None, None, [])

    # Compute distances for all candidates
    candidates_with_distances = []
    for candidate in candidates:
        distance = geodesic_distance(
            captured_lon, captured_lat,
            candidate["lon"], candidate["lat"]
        )
        candidates_with_distances.append((candidate, distance))

    # Sort by distance ascending
    candidates_with_distances.sort(key=lambda x: x[1])

    # Get the closest
    closest_candidate, closest_distance = candidates_with_distances[0]

    return (closest_candidate, closest_distance, candidates_with_distances)


def assign_matches(
    captured_features: list[dict],
    authoritative_features: list[dict],
    type_field_authoritative: str | None,
    type_field_captured: str | None,
    threshold_m: float,
) -> dict[int, dict]:
    """Solve one-to-one captured-to-authoritative matching as a global optimal
    assignment (Hungarian algorithm), instead of resolving each captured
    feature's match independently and greedily.

    Greedy nearest-unclaimed selection (pick_closest_unclaimed) can be fooled
    by a captured cluster sitting at a small, uniform offset from its true
    authoritative counterparts: it silently matches the wrong physical
    features whenever a captured point's *nearest* candidate is a neighbor's
    true match rather than its own. This function instead solves the whole
    batch at once, which provably prefers maximizing the number of real
    matches over minimizing distance among them (see the append-penalty `K`
    below) -- so a locally-tempting-but-wrong match is never taken if it would
    force a worse compensating match, or a spurious append, elsewhere in the
    same batch.

    Args:
        captured_features: List of captured feature dicts (already filtered
            to unprocessed, point-geometry features), each with "lon"/"lat"
            and an "OBJECTID" key.
        authoritative_features: List of unclaimed authoritative feature dicts
            (OID not already claimed this run or in a prior run), each with
            "lon"/"lat" and an "OBJECTID" key.
        type_field_authoritative: Field name in authoritative features for
            type, or None to skip type matching.
        type_field_captured: Field name in captured features for type, or
            None to skip type matching.
        threshold_m: Maximum distance in meters (inclusive) for a real match.

    Returns:
        A dict keyed by each captured feature's index in `captured_features`
        (list-index, not GlobalID -- indices are guaranteed unique, GlobalID
        is not guaranteed non-null). Each value is a dict:
            "matched": bool -- True if assigned a real authoritative match,
                False if assigned to its append/dummy column.
            "authoritative_feature": the assigned authoritative feature dict,
                or None if unmatched.
            "distance_m": the assigned distance, or None if unmatched.
            "candidates": nearest-first list of {"OBJECTID", "GlobalID",
                "distance_m"} dicts for every authoritative feature within
                threshold_m of this captured feature, regardless of who it
                was actually assigned to. Plain Python list, not JSON-encoded
                -- callers building a CSV/report row are responsible for
                json.dumps-ing it (matches existing attachments_added
                precedent in cli.py, keeps this module's return value easy to
                assert on directly in tests).
            "assignment_overridden_nearest": True if the assigned match is
                not the nearest entry in "candidates". Always False for an
                unmatched (appended) row -- there is no assigned match to
                compare against nearest, so False here means "not
                applicable," not "the nearest was chosen." Don't read a
                False on an appended row as "uncontested": its true match
                may still have been stolen by another captured feature in
                the same batch.
    """
    n = len(captured_features)
    if n == 0:
        return {}

    m = len(authoritative_features)

    candidates_per_row = []
    for captured in captured_features:
        candidates = find_candidates(
            authoritative_features,
            captured,
            type_field_authoritative,
            type_field_captured,
            threshold_m,
        )
        _, _, all_sorted = pick_closest(candidates, captured["lon"], captured["lat"])
        candidates_per_row.append(all_sorted)

    # K must strictly exceed the maximum total real cost any full assignment
    # could ever accumulate (min(n, m) * threshold_m), so that maximizing the
    # number of real matches always dominates minimizing distance among them,
    # regardless of which individual distances are involved. Do not simplify
    # this to a bare small constant -- see the proof in
    # docs/2026-07-28-global-optimal-matching-design.md.
    k = threshold_m * (min(n, m) + 1)
    sentinel = k * 10  # must exceed k, which must exceed any feasible real cost

    auth_index_by_oid = {
        auth.get("OBJECTID"): j for j, auth in enumerate(authoritative_features)
    }

    cost = np.full((n, m + n), sentinel, dtype=float)
    for i, all_sorted in enumerate(candidates_per_row):
        for candidate, distance in all_sorted:
            j = auth_index_by_oid[candidate.get("OBJECTID")]
            cost[i][j] = distance
        cost[i][m + i] = k  # this row's own append/dummy column

    row_ind, col_ind = linear_sum_assignment(cost)

    results = {}
    for i, j in zip(row_ind, col_ind):
        all_sorted = candidates_per_row[i]
        candidates_list = [
            {
                "OBJECTID": candidate.get("OBJECTID"),
                "GlobalID": candidate.get("GlobalID"),
                "distance_m": distance,
            }
            for candidate, distance in all_sorted
        ]

        if j < m:
            assert cost[i][j] <= threshold_m, (
                "assign_matches: solver picked an out-of-threshold real match "
                "-- sentinel/K ordering invariant violated"
            )
            authoritative = authoritative_features[j]
            distance_m = cost[i][j]
            nearest_oid = all_sorted[0][0].get("OBJECTID") if all_sorted else None
            results[i] = {
                "matched": True,
                "authoritative_feature": authoritative,
                "distance_m": distance_m,
                "candidates": candidates_list,
                "assignment_overridden_nearest": authoritative.get("OBJECTID") != nearest_oid,
            }
        else:
            results[i] = {
                "matched": False,
                "authoritative_feature": None,
                "distance_m": None,
                "candidates": candidates_list,
                "assignment_overridden_nearest": False,
            }

    return results


def find_candidates(
    authoritative_features: list[dict],
    captured_feature: dict,
    type_field_authoritative: str | None,
    type_field_captured: str | None,
    threshold_m: float
) -> list[dict]:
    """Filter authoritative features by type (if configured) and spatial proximity.

    Returns only features that are within threshold_m meters of the captured
    feature's coordinates. If both type_field_authoritative and
    type_field_captured are given, also requires the type value to match —
    most layers have no type attribute and rely on distance alone.

    Args:
        authoritative_features: List of candidate feature dicts to filter.
        captured_feature: The captured feature (reference point).
        type_field_authoritative: Field name in authoritative features for type,
            or None to skip type matching.
        type_field_captured: Field name in captured feature for type, or None
            to skip type matching.
        threshold_m: Maximum distance in meters (inclusive).

    Returns:
        Filtered list of matching features (order unspecified).
    """
    check_type = type_field_authoritative is not None and type_field_captured is not None
    captured_type = captured_feature[type_field_captured] if check_type else None
    captured_lon = captured_feature["lon"]
    captured_lat = captured_feature["lat"]

    result = []
    for feature in authoritative_features:
        if check_type and feature[type_field_authoritative] != captured_type:
            continue

        # Check distance
        distance = geodesic_distance(
            captured_lon, captured_lat,
            feature["lon"], feature["lat"]
        )
        if distance <= threshold_m:
            result.append(feature)

    return result
