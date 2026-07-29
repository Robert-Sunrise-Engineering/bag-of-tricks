"""Tests for the matching module.

This test suite covers:
- pick_closest: finding and ranking candidates by distance
- find_candidates: filtering by type and spatial proximity
"""

import pytest
from conflate.matching import pick_closest, find_candidates, assign_matches
from conflate.geometry import geodesic_distance

# meters-per-degree at the equator, used to build small lon/lat offsets that
# reproduce specific real-world meter distances via the actual geodesic_distance
_M_PER_DEG = 111320.0


def _m(meters):
    return meters / _M_PER_DEG


class TestPickClosest:
    """Tests for the pick_closest function."""

    def test_pick_closest_empty_list(self):
        """Test pick_closest with an empty candidate list."""
        result = pick_closest([], 0.0, 0.0)
        assert result == (None, None, [])

    def test_pick_closest_single_candidate(self):
        """Test pick_closest with a single candidate."""
        candidates = [{"lon": 0.00001, "lat": 0.0, "name": "candidate1"}]
        closest, distance, all_sorted = pick_closest(candidates, 0.0, 0.0)

        assert closest == candidates[0]
        assert distance == pytest.approx(geodesic_distance(0.0, 0.0, 0.00001, 0.0))
        assert len(all_sorted) == 1
        assert all_sorted[0][0] == candidates[0]

    def test_pick_closest_multiple_candidates_sorted(self):
        """Test that pick_closest returns all candidates sorted by distance (Fixture C).

        Two same-type in-threshold candidates at different known distances.
        Confirm pick_closest picks the genuinely closer one and returns sorted list.
        """
        # Create a captured point at origin
        captured_lon, captured_lat = 0.0, 0.0

        # Create two candidates at different distances from captured point
        # Candidate A: further away but still close (shifted by 0.0001 degrees lon)
        candidate_a = {"lon": 0.0001, "lat": 0.0, "type": "point", "id": "A"}
        distance_a = geodesic_distance(captured_lon, captured_lat,
                                       candidate_a["lon"], candidate_a["lat"])

        # Candidate B: much closer (shifted by 0.00001 degrees lon)
        candidate_b = {"lon": 0.00001, "lat": 0.0, "type": "point", "id": "B"}
        distance_b = geodesic_distance(captured_lon, captured_lat,
                                       candidate_b["lon"], candidate_b["lat"])

        # Sanity check: B should be closer than A
        assert distance_b < distance_a

        candidates = [candidate_a, candidate_b]
        closest, closest_distance, all_sorted = pick_closest(candidates, captured_lon, captured_lat)

        # Should pick the closer one (B)
        assert closest == candidate_b
        assert closest_distance == pytest.approx(distance_b)

        # all_sorted should have 2 entries, sorted by distance
        assert len(all_sorted) == 2
        assert all_sorted[0][0] == candidate_b
        assert all_sorted[0][1] == pytest.approx(distance_b)
        assert all_sorted[1][0] == candidate_a
        assert all_sorted[1][1] == pytest.approx(distance_a)
        # Confirm sorting: first distance < second distance
        assert all_sorted[0][1] < all_sorted[1][1]


class TestFindCandidates:
    """Tests for the find_candidates function."""

    def test_find_candidates_same_type_within_threshold(self):
        """Test find_candidates with 2+ same-type candidates, one within threshold (Fixture A).

        A captured point with 2+ same-type authoritative candidates, where exactly
        one is within threshold_m. Call find_candidates FIRST, then pick_closest
        on the result.
        """
        # Captured point at origin
        captured_feature = {
            "lon": 0.0,
            "lat": 0.0,
            "type": "park",
            "name": "captured"
        }

        # Authoritative features: 2 of same type, one within threshold, one outside
        # Candidate 1: ~11 meters away (0.0001 degrees)
        candidate_1 = {
            "lon": 0.0001,
            "lat": 0.0,
            "type": "park",
            "id": "auth_1"
        }

        # Candidate 2: very far away (0.01 degrees ≈ 1111 meters)
        candidate_2 = {
            "lon": 0.01,
            "lat": 0.0,
            "type": "park",
            "id": "auth_2"
        }

        authoritative_features = [candidate_1, candidate_2]
        threshold_m = 50.0  # Only candidate_1 should be within this

        # Call find_candidates with the full list
        result = find_candidates(
            authoritative_features,
            captured_feature,
            type_field_authoritative="type",
            type_field_captured="type",
            threshold_m=threshold_m
        )

        # Should return only candidate_1
        assert len(result) == 1
        assert result[0] == candidate_1

        # Now call pick_closest on the filtered result
        closest, distance, all_sorted = pick_closest(result,
                                                      captured_feature["lon"],
                                                      captured_feature["lat"])
        assert closest == candidate_1
        assert distance == pytest.approx(
            geodesic_distance(captured_feature["lon"], captured_feature["lat"],
                            candidate_1["lon"], candidate_1["lat"])
        )

    def test_find_candidates_only_type_outside_threshold(self):
        """Test find_candidates where only same-type candidate is outside threshold (Fixture B).

        A captured point where the only candidate of the same type is OUTSIDE
        threshold_m. Confirm find_candidates returns an empty list.
        """
        captured_feature = {
            "lon": 0.0,
            "lat": 0.0,
            "type": "hospital",
            "name": "captured"
        }

        # Only one hospital candidate, far away (0.01 degrees ≈ 1111 meters)
        candidate_hospital = {
            "lon": 0.01,
            "lat": 0.0,
            "type": "hospital",
            "id": "hospital_1"
        }

        authoritative_features = [candidate_hospital]
        threshold_m = 50.0  # Candidate is much further than this

        result = find_candidates(
            authoritative_features,
            captured_feature,
            type_field_authoritative="type",
            type_field_captured="type",
            threshold_m=threshold_m
        )

        # Should return empty list
        assert result == []

    def test_find_candidates_type_exclusion_with_mixed_types(self):
        """Test that type matching is enforced with mixed-type list (Fixture D).

        THE IMPORTANT ONE: A captured point where a DIFFERENT-type candidate is
        spatially CLOSER to the captured point than any same-type candidate.
        Build a mixed-type authoritative_features list (same-type candidate further
        away but within threshold, different-type candidate very close but wrong type)
        and call find_candidates on this MIXED list (NOT a pre-filtered list).
        Confirm the returned list does NOT include the closer wrong-type candidate,
        i.e., the type exclusion is actually happening inside find_candidates itself.
        """
        # Captured point at origin
        captured_feature = {
            "lon": 0.0,
            "lat": 0.0,
            "type": "restaurant",
            "name": "captured"
        }

        # Different-type candidate: VERY CLOSE (0.00001 degrees ≈ 1.1 meters) but wrong type
        wrong_type_close = {
            "lon": 0.00001,
            "lat": 0.0,
            "type": "cafe",  # Different type!
            "id": "cafe_1"
        }
        distance_wrong_type = geodesic_distance(
            captured_feature["lon"], captured_feature["lat"],
            wrong_type_close["lon"], wrong_type_close["lat"]
        )

        # Same-type candidate: FURTHER AWAY (0.0001 degrees ≈ 11 meters) but correct type
        same_type_far = {
            "lon": 0.0001,
            "lat": 0.0,
            "type": "restaurant",  # Same type as captured
            "id": "restaurant_1"
        }
        distance_same_type = geodesic_distance(
            captured_feature["lon"], captured_feature["lat"],
            same_type_far["lon"], same_type_far["lat"]
        )

        # Verify that wrong-type is actually closer
        assert distance_wrong_type < distance_same_type

        # Create a genuinely MIXED authoritative list (not pre-filtered)
        authoritative_features = [wrong_type_close, same_type_far]
        threshold_m = 50.0  # Both are within threshold, but only same-type should pass

        # Call find_candidates on the MIXED list
        result = find_candidates(
            authoritative_features,
            captured_feature,
            type_field_authoritative="type",
            type_field_captured="type",
            threshold_m=threshold_m
        )

        # Should return only the same-type candidate, NOT the closer wrong-type one
        assert len(result) == 1
        assert result[0] == same_type_far
        assert result[0]["type"] == "restaurant"

        # Verify the wrong-type is not in the result
        assert wrong_type_close not in result

    def test_find_candidates_no_type_fields_matches_on_distance_only(self):
        """Test find_candidates with type_field_authoritative/type_field_captured
        omitted (None): the closer wrong-type candidate from Fixture D should now
        be included, since there's no type filter to exclude it.
        """
        captured_feature = {
            "lon": 0.0,
            "lat": 0.0,
            "type": "restaurant",
            "name": "captured"
        }

        wrong_type_close = {
            "lon": 0.00001,
            "lat": 0.0,
            "type": "cafe",
            "id": "cafe_1"
        }

        same_type_far = {
            "lon": 0.0001,
            "lat": 0.0,
            "type": "restaurant",
            "id": "restaurant_1"
        }

        authoritative_features = [wrong_type_close, same_type_far]
        threshold_m = 50.0

        result = find_candidates(
            authoritative_features,
            captured_feature,
            type_field_authoritative=None,
            type_field_captured=None,
            threshold_m=threshold_m
        )

        # Both are within threshold; with no type filter, both should be returned.
        assert len(result) == 2
        ids = {f["id"] for f in result}
        assert ids == {"cafe_1", "restaurant_1"}

    def test_find_candidates_multiple_threshold(self):
        """Test find_candidates respects the distance threshold correctly."""
        captured_feature = {
            "lon": 0.0,
            "lat": 0.0,
            "type": "school",
            "name": "captured"
        }

        # Create candidates at increasing distances
        candidates_data = [
            {"lon": 0.00001, "lat": 0.0, "type": "school", "id": "school_1"},  # ~1.1m
            {"lon": 0.0001, "lat": 0.0, "type": "school", "id": "school_2"},   # ~11m
            {"lon": 0.001, "lat": 0.0, "type": "school", "id": "school_3"},    # ~111m
        ]

        authoritative_features = candidates_data

        # With tight threshold: only school_1
        result_10m = find_candidates(
            authoritative_features,
            captured_feature,
            type_field_authoritative="type",
            type_field_captured="type",
            threshold_m=10.0
        )
        assert len(result_10m) == 1
        assert result_10m[0]["id"] == "school_1"

        # With medium threshold: school_1 and school_2
        result_50m = find_candidates(
            authoritative_features,
            captured_feature,
            type_field_authoritative="type",
            type_field_captured="type",
            threshold_m=50.0
        )
        assert len(result_50m) == 2
        ids_50m = {f["id"] for f in result_50m}
        assert ids_50m == {"school_1", "school_2"}

        # With large threshold: all candidates
        result_200m = find_candidates(
            authoritative_features,
            captured_feature,
            type_field_authoritative="type",
            type_field_captured="type",
            threshold_m=200.0
        )
        assert len(result_200m) == 3


class TestIntegration:
    """Integration tests combining find_candidates and pick_closest."""

    def test_find_and_pick_workflow(self):
        """Test the typical workflow: find_candidates then pick_closest."""
        captured_feature = {
            "lon": 0.0,
            "lat": 0.0,
            "type": "bridge",
            "name": "captured"
        }

        authoritative_features = [
            {"lon": 0.00001, "lat": 0.0, "type": "bridge", "id": "bridge_1"},
            {"lon": 0.0001, "lat": 0.0, "type": "bridge", "id": "bridge_2"},
            {"lon": 0.01, "lat": 0.0, "type": "bridge", "id": "bridge_3"},  # Far
            {"lon": 0.00005, "lat": 0.0, "type": "road", "id": "road_1"},   # Different type
        ]

        # Find candidates within 100m with matching type
        candidates = find_candidates(
            authoritative_features,
            captured_feature,
            type_field_authoritative="type",
            type_field_captured="type",
            threshold_m=100.0
        )

        # Should have bridge_1 and bridge_2, but not bridge_3 (too far) or road_1 (wrong type)
        assert len(candidates) == 2
        ids = {f["id"] for f in candidates}
        assert ids == {"bridge_1", "bridge_2"}

        # Pick the closest one
        closest, distance, all_sorted = pick_closest(
            candidates,
            captured_feature["lon"],
            captured_feature["lat"]
        )

        # bridge_1 should be closest
        assert closest["id"] == "bridge_1"
        assert len(all_sorted) == 2
        assert all_sorted[0][0]["id"] == "bridge_1"
        assert all_sorted[1][0]["id"] == "bridge_2"


class TestAssignMatches:
    """Tests for assign_matches, the global-optimal replacement for
    pick_closest_unclaimed's greedy per-feature claiming.

    Fixture coordinates reproduce (in real lon/lat degrees, via the actual
    geodesic_distance -- not the flat-plane meters used in the design doc's
    worked examples) the same two adversarial patterns documented in
    docs/2026-07-28-global-optimal-matching-design.md.
    """

    def test_row_of_four_avoids_greedys_forced_spurious_append(self):
        """Authoritative points 4m apart on a line, captured points each
        shifted +3m along it. Greedy claiming (pick_closest_unclaimed,
        processed in order) mismatches C1-C3 by one index each and leaves C4
        unmatched -- despite every individual "match" looking clean (~1m,
        comfortably under the 10.67m threshold) and C4 having a perfectly
        good true match (A4, ~3m) that gets stolen. assign_matches must
        instead return the true diagonal assignment: all 4 correct, ~3m
        each, nobody appended.
        """
        threshold_m = 10.67

        authoritative = [
            {"lon": _m(i * 4), "lat": 0.0, "OBJECTID": i + 1, "GlobalID": f"A{i + 1}"}
            for i in range(4)
        ]
        captured = [
            {"lon": _m(i * 4 + 3), "lat": 0.0, "OBJECTID": 100 + i, "GlobalID": f"C{i + 1}"}
            for i in range(4)
        ]

        results = assign_matches(captured, authoritative, None, None, threshold_m)

        assert len(results) == 4
        for i in range(4):
            result = results[i]
            assert result["matched"] is True, f"C{i + 1} should be matched, not appended"
            assert result["authoritative_feature"]["GlobalID"] == f"A{i + 1}"
            expected_distance = geodesic_distance(
                captured[i]["lon"], captured[i]["lat"],
                authoritative[i]["lon"], authoritative[i]["lat"],
            )
            assert result["distance_m"] == pytest.approx(expected_distance)
            assert result["distance_m"] == pytest.approx(3.0, abs=0.01)

    def test_diamond_true_match_not_nearest_still_wins_globally(self):
        """A diamond of authoritative points (N/S/E/W), captured points each
        shifted southeast. N_cap's and W_cap's true match is NOT their
        nearest candidate (a wrong point is closer) -- greedy claiming would
        grab that locally-tempting wrong match and cascade the damage onto
        whichever row later loses its own true match. assign_matches must
        still return the true diagonal assignment, since it's the unique
        global-minimum-cost full assignment.
        """
        threshold_m = 30.0  # wide enough to keep every pairwise distance feasible

        auth_points = {"N": (0, 10), "S": (0, -10), "E": (5, 0), "W": (-5, 0)}
        shift = (7, -7)

        authoritative = [
            {"lon": _m(x), "lat": _m(y), "OBJECTID": i + 1, "GlobalID": name}
            for i, (name, (x, y)) in enumerate(auth_points.items())
        ]
        captured = [
            {
                "lon": _m(x + shift[0]),
                "lat": _m(y + shift[1]),
                "OBJECTID": 100 + i,
                "GlobalID": f"{name}_cap",
            }
            for i, (name, (x, y)) in enumerate(auth_points.items())
        ]

        results = assign_matches(captured, authoritative, None, None, threshold_m)

        assert len(results) == 4
        for i, (name, _) in enumerate(auth_points.items()):
            result = results[i]
            assert result["matched"] is True
            assert result["authoritative_feature"]["GlobalID"] == name, (
                f"{name}_cap should match its true counterpart {name}, "
                "not a locally-closer wrong candidate"
            )

        # N_cap and W_cap: true match is NOT their nearest candidate --
        # assignment_overridden_nearest must be True.
        assert results[0]["assignment_overridden_nearest"] is True  # N_cap
        assert results[3]["assignment_overridden_nearest"] is True  # W_cap

        # S_cap and E_cap: true match IS already their nearest candidate --
        # assignment_overridden_nearest must be False.
        assert results[1]["assignment_overridden_nearest"] is False  # S_cap
        assert results[2]["assignment_overridden_nearest"] is False  # E_cap

    def test_empty_captured_features_returns_empty_dict(self):
        """Test that assign_matches returns empty dict when captured_features is empty."""
        result = assign_matches([], [{"lon": 0.0, "lat": 0.0, "OBJECTID": 1, "GlobalID": "A1"}], None, None, 10.0)
        assert result == {}

    def test_empty_authoritative_features_all_appended(self):
        """Test that assign_matches returns unmatched entries when no authoritative features.

        Build 3 captured features and call assign_matches with empty authoritative list.
        Verify all are marked as unmatched with no candidates.
        """
        captured = [
            {"lon": 0.0, "lat": 0.0, "OBJECTID": 100, "GlobalID": "C1"},
            {"lon": _m(5), "lat": 0.0, "OBJECTID": 101, "GlobalID": "C2"},
            {"lon": _m(10), "lat": 0.0, "OBJECTID": 102, "GlobalID": "C3"},
        ]

        results = assign_matches(captured, [], None, None, 10.0)

        assert len(results) == 3
        for i in range(3):
            assert results[i]["matched"] is False
            assert results[i]["authoritative_feature"] is None
            assert results[i]["distance_m"] is None
            assert results[i]["candidates"] == []

    def test_type_field_exclusion_respected(self):
        """Test that type field filtering excludes closer wrong-type candidates.

        Build 1 captured feature with type "hydrant" and 2 authoritative features:
        - one with matching type at 1m away
        - one with wrong type at 0.5m away (closer!)

        Verify the right-type candidate is matched, and the wrong-type is excluded
        from the candidates list entirely (not just unmatched).
        """
        captured = [
            {"lon": 0.0, "lat": 0.0, "OBJECTID": 100, "GlobalID": "C1", "type": "hydrant"}
        ]

        authoritative_wrong_type = {
            "lon": _m(0.5), "lat": 0.0, "OBJECTID": 2, "GlobalID": "A2", "type": "valve"
        }
        authoritative_right_type = {
            "lon": _m(1), "lat": 0.0, "OBJECTID": 1, "GlobalID": "A1", "type": "hydrant"
        }

        results = assign_matches(
            captured,
            [authoritative_wrong_type, authoritative_right_type],
            "type",
            "type",
            10.0
        )

        assert len(results) == 1
        assert results[0]["matched"] is True
        assert results[0]["authoritative_feature"]["GlobalID"] == "A1"
        # Candidates list should only have the right-type feature (length 1)
        assert len(results[0]["candidates"]) == 1
        assert results[0]["candidates"][0]["GlobalID"] == "A1"
