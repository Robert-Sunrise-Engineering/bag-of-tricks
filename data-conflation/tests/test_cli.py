"""Tests for conflate.cli helpers.

Covers _simplify_feature/_has_point_geometry (the null/non-point geometry
guard) and _attachments_fully_succeeded (the attachment-status ledgering
decision), without exercising main()'s full AGOL-connected pipeline.
"""

from conflate.cli import (
    _simplify_feature,
    _has_point_geometry,
    _attachments_fully_succeeded,
    _seed_claimed_oids,
)


class TestHasPointGeometry:
    def test_point_feature_has_geometry(self):
        raw = {"attributes": {"GlobalID": "g1"}, "geometry": {"x": 1.0, "y": 2.0}}
        simplified = _simplify_feature(raw)
        assert _has_point_geometry(simplified) is True

    def test_missing_geometry_key_has_no_geometry(self):
        raw = {"attributes": {"GlobalID": "g1"}}
        simplified = _simplify_feature(raw)
        assert _has_point_geometry(simplified) is False

    def test_null_geometry_has_no_geometry(self):
        raw = {"attributes": {"GlobalID": "g1"}, "geometry": None}
        simplified = _simplify_feature(raw)
        assert _has_point_geometry(simplified) is False

    def test_non_point_geometry_has_no_geometry(self):
        """A polyline/polygon geometry (rings/paths, no x/y) must be treated
        as having no usable point location, not crash downstream in
        geodesic_distance."""
        raw = {
            "attributes": {"GlobalID": "g1"},
            "geometry": {"paths": [[[0.0, 0.0], [1.0, 1.0]]]},
        }
        simplified = _simplify_feature(raw)
        assert _has_point_geometry(simplified) is False

    def test_partial_geometry_missing_y_has_no_geometry(self):
        raw = {"attributes": {"GlobalID": "g1"}, "geometry": {"x": 1.0}}
        simplified = _simplify_feature(raw)
        assert _has_point_geometry(simplified) is False


class TestSeedClaimedOids:
    def test_empty_ledger_seeds_nothing(self):
        assert _seed_claimed_oids({}) == set()

    def test_seeds_from_prior_run_ledger_entries(self):
        """The exact scenario from the review finding: a captured feature A
        matched authoritative OID 5 in a prior run. That OID must come back
        pre-claimed so a different, newly-captured feature B can't re-claim
        (and silently merge into) it this run."""
        ledger = {
            "gA": {"action": "updated", "authoritative_oid": 5, "attachments_status": "0/0", "run_time": "t"},
            "gB": {"action": "created", "authoritative_oid": 9, "attachments_status": "0/0", "run_time": "t"},
        }
        assert _seed_claimed_oids(ledger) == {5, 9}

    def test_entries_with_no_authoritative_oid_are_skipped(self):
        ledger = {
            "gA": {"action": "updated", "authoritative_oid": None, "attachments_status": "0/0", "run_time": "t"},
        }
        assert _seed_claimed_oids(ledger) == set()


class TestAttachmentsFullySucceeded:
    def test_none_status_is_not_success(self):
        """None (returned by copy_attachments when attachment listing itself
        failed) must never be treated as full success."""
        assert _attachments_fully_succeeded(None) is False

    def test_zero_zero_is_success(self):
        assert _attachments_fully_succeeded("0/0") is True

    def test_matching_counts_is_success(self):
        assert _attachments_fully_succeeded("3/3") is True

    def test_partial_is_not_success(self):
        assert _attachments_fully_succeeded("2/3") is False
