"""Matching module for conflation operations.

Provides functions for finding and ranking candidate features for conflation
based on spatial proximity and type matching.
"""

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


def pick_closest_unclaimed(
    candidates: list[dict], captured_lon: float, captured_lat: float, claimed_oids: set
) -> tuple:
    """Like pick_closest, but skips candidates whose OBJECTID is already in claimed_oids.

    Used to enforce one-to-one matching within a single run: once an
    authoritative feature has been claimed by an earlier captured feature, it
    is no longer eligible to be picked as the closest match for a later one.

    Args:
        candidates: List of candidate feature dicts, each with "lon", "lat",
            and "OBJECTID" keys.
        captured_lon: Longitude of the captured feature.
        captured_lat: Latitude of the captured feature.
        claimed_oids: Set of authoritative OBJECTIDs already claimed this run.

    Returns:
        A 3-tuple: (closest_unclaimed_candidate, closest_unclaimed_distance,
        all_candidates_with_distances_sorted). The first two elements are the
        closest candidate/distance NOT in claimed_oids, or (None, None) if
        every candidate is already claimed. The third element is the full,
        unfiltered distance-sorted list (same as pick_closest), regardless of
        claims.
    """
    _, _, all_sorted = pick_closest(candidates, captured_lon, captured_lat)

    for candidate, distance in all_sorted:
        if candidate.get("OBJECTID") not in claimed_oids:
            return (candidate, distance, all_sorted)

    return (None, None, all_sorted)


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
