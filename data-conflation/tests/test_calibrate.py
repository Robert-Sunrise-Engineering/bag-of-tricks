"""Tests for conflate.calibrate -- unsupervised per-layer threshold
calibration (nearest_distances / suggest_threshold).
"""

import pytest

from conflate.calibrate import nearest_distances, suggest_threshold
from conflate.geometry import geodesic_distance

# meters-per-degree at the equator, used to build small lon/lat offsets that
# reproduce specific real-world meter distances via the actual geodesic_distance
# (same convention as tests/test_matching.py).
_M_PER_DEG = 111320.0


def _m(meters):
    return meters / _M_PER_DEG


class TestSuggestThreshold:
    def test_clear_bimodal_valley_lands_between_clusters(self):
        # A tight near cluster (true correspondences, ~1.8-2.2m) and a
        # diffuse far cluster (unmatched captured features, ~70-92m) --
        # orders of magnitude apart, exactly the shape the log-space KDE
        # valley detector is designed for.
        near = [1.8, 1.9, 2.0, 2.0, 2.1, 2.2, 1.85, 1.95, 2.05, 2.15,
                1.9, 2.0, 2.1, 1.8, 2.2, 1.95, 2.05, 1.85, 2.15, 1.9]
        far = [70, 75, 80, 85, 90, 72, 78, 82, 88, 92,
               74, 76, 84, 86, 91, 73, 79, 81, 87, 89]
        result = suggest_threshold(near + far)

        assert result["confidence"] == "clear_bimodal_valley"
        assert result["n_samples"] == 40
        assert result["suggested_threshold_m"] is not None
        # The valley must fall strictly between the two clusters.
        assert max(near) < result["suggested_threshold_m"] < min(far)
        assert result["fraction_within_suggested"] == pytest.approx(0.5)
        assert result["distance_summary"]["min"] == pytest.approx(min(near))
        assert result["distance_summary"]["max"] == pytest.approx(max(far))

    def test_unimodal_distribution_falls_back_to_mad(self):
        # A single, evenly-spread cluster -- no real separation to find a
        # valley in, so the robust median+3*MAD fallback must trigger.
        distances = [float(x) for x in range(5, 25)]  # 5..24, one mode
        result = suggest_threshold(distances)

        assert result["confidence"] == "low_no_clear_bimodal_separation"
        assert result["n_samples"] == 20
        assert result["suggested_threshold_m"] is not None
        assert result["suggested_threshold_m"] > max(distances) * 0.4

    def test_empty_distances_is_insufficient_data(self):
        result = suggest_threshold([])
        assert result["confidence"] == "insufficient_data"
        assert result["suggested_threshold_m"] is None
        assert result["n_samples"] == 0
        assert result["fraction_within_suggested"] is None
        assert result["distance_summary"] is None

    def test_below_min_samples_is_insufficient_data_but_summarizes(self):
        # Too few samples to trust a density estimate, but there's still
        # enough data to report a distance_summary for diagnostics.
        distances = [1.0, 2.0, 3.0]
        result = suggest_threshold(distances, min_samples=8)
        assert result["confidence"] == "insufficient_data"
        assert result["suggested_threshold_m"] is None
        assert result["n_samples"] == 3
        assert result["distance_summary"] is not None
        assert result["distance_summary"]["median"] == pytest.approx(2.0)

    def test_custom_min_samples_threshold(self):
        distances = [1.0, 2.0, 3.0, 4.0, 5.0]
        # With a lower min_samples, the same 5 samples are enough to attempt
        # (and here, unimodal) calibration instead of being insufficient.
        result = suggest_threshold(distances, min_samples=5)
        assert result["confidence"] != "insufficient_data"


class TestNearestDistances:
    def test_nearest_distance_per_captured_feature_no_type_filter(self):
        captured = [{"lon": 0.0, "lat": 0.0}]
        authoritative = [
            {"lon": _m(50), "lat": 0.0},
            {"lon": _m(5), "lat": 0.0},
            {"lon": _m(100), "lat": 0.0},
        ]
        distances = nearest_distances(captured, authoritative, None, None)
        assert len(distances) == 1
        assert distances[0] == pytest.approx(
            geodesic_distance(0.0, 0.0, _m(5), 0.0), rel=1e-6
        )

    def test_type_field_filters_candidates(self):
        # The nearest overall candidate is a type mismatch; the nearest
        # *type-matching* candidate is farther away and must be the one
        # picked.
        captured = [{"lon": 0.0, "lat": 0.0, "kind": "hydrant"}]
        authoritative = [
            {"lon": _m(1), "lat": 0.0, "kind": "valve"},
            {"lon": _m(20), "lat": 0.0, "kind": "hydrant"},
        ]
        distances = nearest_distances(captured, authoritative, "kind", "kind")
        assert len(distances) == 1
        assert distances[0] == pytest.approx(
            geodesic_distance(0.0, 0.0, _m(20), 0.0), rel=1e-6
        )

    def test_captured_feature_with_no_type_match_is_skipped(self):
        captured = [
            {"lon": 0.0, "lat": 0.0, "kind": "hydrant"},
            {"lon": 0.0, "lat": 0.0, "kind": "manhole"},
        ]
        authoritative = [{"lon": _m(1), "lat": 0.0, "kind": "hydrant"}]
        distances = nearest_distances(captured, authoritative, "kind", "kind")
        # Only the hydrant-typed captured feature has a candidate; the
        # manhole one is skipped rather than raising or returning None.
        assert len(distances) == 1

    def test_empty_authoritative_list_skips_all_captured(self):
        captured = [{"lon": 0.0, "lat": 0.0}]
        distances = nearest_distances(captured, [], None, None)
        assert distances == []

    def test_empty_captured_list_returns_empty(self):
        authoritative = [{"lon": 0.0, "lat": 0.0}]
        distances = nearest_distances([], authoritative, None, None)
        assert distances == []
