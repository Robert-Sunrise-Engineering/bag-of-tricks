"""Tests for conflate.verify.verify_restore.

verify_restore closes a gap in rollback's own bookkeeping: apply_updates'
success/failure only reflects whether AGOL *accepted* a restore write, not
whether the live feature's state actually matches the backup snapshot it
was restored from. These tests exercise the diff logic directly, without
going through rollback.py (see tests/test_rollback.py for the wiring).
"""

from types import SimpleNamespace

from conflate.verify import verify_restore, DEFAULT_GEOMETRY_TOLERANCE_M


class _FakeFeature:
    def __init__(self, raw):
        self._raw = raw

    def as_dict(self):
        return self._raw


class FakeVerifyLayer:
    """Answers verify_restore's OBJECTID IN (...) attribute-fetch query with
    canned live features, regardless of the where-clause's exact content."""

    def __init__(self, live_features):
        self.live_features = live_features
        self.query_calls = []

    def query(self, where=None, out_fields=None, out_sr=None,
              return_all_records=None, return_count_only=False):
        self.query_calls.append(where)
        return SimpleNamespace(features=[_FakeFeature(f) for f in self.live_features])


def _backup_entry(oid=5, notes="original notes", lon=-122.0, lat=45.0, wkid=4326):
    return {
        "oid": oid,
        "attributes": {
            "OBJECTID": oid,
            "GlobalID": "{aaaa}",
            "Editor": "someone",
            "EditDate": 1700000000000,
            "Notes": notes,
        },
        "geometry": {"x": lon, "y": lat, "spatialReference": {"wkid": wkid}},
    }


def _live_feature(oid=5, notes="original notes", lon=-122.0, lat=45.0,
                   editor="somebody-else", edit_date=1800000000000, wkid=4326):
    return {
        "attributes": {
            "OBJECTID": oid,
            "GlobalID": "{aaaa}",
            "Editor": editor,
            "EditDate": edit_date,
            "Notes": notes,
        },
        "geometry": {"x": lon, "y": lat, "spatialReference": {"wkid": wkid}},
    }


def test_empty_backup_entries_returns_empty_list():
    layer = FakeVerifyLayer([])
    assert verify_restore(layer, []) == []
    assert layer.query_calls == []  # no need to query AGOL for nothing


def test_matching_live_state_is_verified():
    entry = _backup_entry()
    layer = FakeVerifyLayer([_live_feature()])

    results = verify_restore(layer, [entry])

    assert results == [{
        "oid": 5,
        "live_feature_found": True,
        "verified": True,
        "attribute_mismatches": {},
        "geometry_mismatch_m": 0.0,
    }]


def test_attribute_mismatch_is_reported():
    entry = _backup_entry(notes="original notes")
    layer = FakeVerifyLayer([_live_feature(notes="never restored")])

    [result] = verify_restore(layer, [entry])

    assert result["verified"] is False
    assert result["attribute_mismatches"] == {"Notes": ("original notes", "never restored")}


def test_excluded_fields_never_flagged_even_when_different():
    """Editor/EditDate always differ after a real restore write (AGOL sets
    them itself) -- that's exactly why fields.EXCLUDED_FIELDS exists, and
    verify_restore must apply the same exclusion rollback.py's restore
    payload does."""
    entry = _backup_entry()
    layer = FakeVerifyLayer([
        _live_feature(editor="agol-managed-editor", edit_date=9999999999999)
    ])

    [result] = verify_restore(layer, [entry])

    assert result["verified"] is True
    assert result["attribute_mismatches"] == {}


def test_geometry_mismatch_beyond_tolerance_is_reported():
    entry = _backup_entry(lat=45.0)
    # ~111m north -- far beyond the default 0.5m tolerance.
    layer = FakeVerifyLayer([_live_feature(lat=45.001)])

    [result] = verify_restore(layer, [entry])

    assert result["verified"] is False
    assert result["attribute_mismatches"] == {}
    assert result["geometry_mismatch_m"] > DEFAULT_GEOMETRY_TOLERANCE_M


def test_geometry_within_tolerance_is_verified():
    entry = _backup_entry(lat=45.0)
    # A sub-centimeter offset (reprojection rounding) must not be flagged.
    layer = FakeVerifyLayer([_live_feature(lat=45.00000001)])

    [result] = verify_restore(layer, [entry])

    assert result["verified"] is True
    assert 0 < result["geometry_mismatch_m"] < DEFAULT_GEOMETRY_TOLERANCE_M


def test_differing_spatial_reference_is_reported_without_computing_distance():
    """Both sides are expected to be WGS84 (out_sr=4326 on both the original
    fetch and verify_restore's own query) -- if that invariant ever breaks,
    raw x/y from two different spatial references must not be silently fed
    to geodesic_distance as if they were both degrees."""
    entry = _backup_entry(wkid=4326)
    layer = FakeVerifyLayer([_live_feature(wkid=102100)])  # e.g. Web Mercator

    [result] = verify_restore(layer, [entry])

    assert result["verified"] is False
    assert result["geometry_mismatch_m"] is None


def test_missing_live_feature_is_reported_not_found():
    entry = _backup_entry(oid=5)
    layer = FakeVerifyLayer([])  # OID 5 no longer exists live

    [result] = verify_restore(layer, [entry])

    assert result == {
        "oid": 5,
        "live_feature_found": False,
        "verified": False,
        "attribute_mismatches": {},
        "geometry_mismatch_m": None,
    }


def test_oids_are_batched_to_avoid_one_giant_where_clause():
    """water_service_connections restored 359 OIDs in the 2026-07-27 run --
    a single unbounded "OBJECTID IN (...)" clause isn't something to
    assume is safe over POST. 201 entries must span two query() calls at
    the 200-per-batch size."""
    entries = [_backup_entry(oid=oid) for oid in range(1, 202)]
    layer = FakeVerifyLayer([_live_feature(oid=oid) for oid in range(1, 202)])

    results = verify_restore(layer, entries)

    assert len(results) == 201
    assert all(r["verified"] for r in results)
    assert len(layer.query_calls) == 2


def test_multiple_entries_matched_by_oid_independently():
    entries = [_backup_entry(oid=5, notes="a"), _backup_entry(oid=6, notes="b")]
    layer = FakeVerifyLayer([
        _live_feature(oid=5, notes="a"),  # matches
        _live_feature(oid=6, notes="WRONG"),  # mismatches
    ])

    results = {r["oid"]: r for r in verify_restore(layer, entries)}

    assert results[5]["verified"] is True
    assert results[6]["verified"] is False
