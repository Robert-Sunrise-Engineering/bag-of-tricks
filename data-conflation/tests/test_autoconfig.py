"""Tests for conflate.autoconfig -- the pure matching/merge logic behind
``--auto-configure``.

No ``arcgis`` import here: these tests consume only the plain sublayer dicts
that ``conflate.gis_client.list_service_sublayers`` returns and assert the
merge produces layer-config dicts that ``conflate.config.validate_layer_config``
accepts.
"""

import pytest

from conflate.autoconfig import (
    match_sublayers,
    classify_matches,
    build_layer_entry,
    merge_layers_config,
    partition_point_matches,
)
from conflate.config import validate_layer_config

POINT = "esriGeometryPoint"
POLY = "esriGeometryPolygon"


def _sub(name, *, url, geometry_type=POINT, layer_id=0):
    """Build a sublayer dict in the shape list_service_sublayers returns."""
    return {"id": layer_id, "name": name, "geometry_type": geometry_type, "url": url}


def _match(name, a_url, c_url, a_geom=POINT, c_geom=POINT):
    return {
        "name": name,
        "authoritative_url": a_url,
        "captured_url": c_url,
        "authoritative_geometry_type": a_geom,
        "captured_geometry_type": c_geom,
    }


class TestMatchSublayers:
    def test_exact_match_pairs_shared_names(self):
        auth = [_sub("water_hydrants", url="A/0", layer_id=0), _sub("water_valves", url="A/1", layer_id=1)]
        cap = [_sub("water_hydrants", url="C/0", layer_id=0), _sub("water_valves", url="C/1", layer_id=1)]
        r = match_sublayers(auth, cap)
        assert sorted(m["name"] for m in r["matched"]) == ["water_hydrants", "water_valves"]
        assert r["matched"][0]["authoritative_url"] == "A/0"
        assert r["matched"][0]["captured_url"] == "C/0"
        assert r["auth_only"] == []
        assert r["captured_only"] == []
        assert r["ambiguous"] == []

    def test_auth_only_and_captured_only(self):
        auth = [_sub("only_auth", url="A/0")]
        cap = [_sub("only_cap", url="C/0")]
        r = match_sublayers(auth, cap)
        assert r["matched"] == []
        assert r["auth_only"] == ["only_auth"]
        assert r["captured_only"] == ["only_cap"]
        assert r["ambiguous"] == []

    def test_ambiguous_name_on_one_side_excluded_everywhere(self):
        # 'dup' appears twice on the authoritative side -> ambiguous, not matched
        # even though it also appears once on the captured side.
        auth = [_sub("dup", url="A/0", layer_id=0), _sub("dup", url="A/1", layer_id=1)]
        cap = [_sub("dup", url="C/0", layer_id=0)]
        r = match_sublayers(auth, cap)
        assert r["matched"] == []
        assert r["ambiguous"] == [
            {"authoritative_names": ["dup", "dup"], "captured_names": ["dup"]}
        ]
        assert r["auth_only"] == []
        assert r["captured_only"] == []

    def test_ambiguous_name_on_both_sides(self):
        auth = [_sub("dup", url="A/0", layer_id=0), _sub("dup", url="A/1", layer_id=1)]
        cap = [_sub("dup", url="C/0", layer_id=0), _sub("dup", url="C/1", layer_id=1)]
        r = match_sublayers(auth, cap)
        assert r["matched"] == []
        assert r["ambiguous"] == [
            {"authoritative_names": ["dup", "dup"], "captured_names": ["dup", "dup"]}
        ]

    def test_ambiguous_reports_verbatim_colliding_names(self):
        # Two sublayers collide case-insensitively under different casing --
        # the ambiguous entry must carry the real, searchable names, not the
        # lowercased canonical key.
        auth = [_sub("Water_Valve", url="A/0", layer_id=0), _sub("water_valve", url="A/1", layer_id=1)]
        cap = [_sub("water_valve", url="C/0", layer_id=0)]
        r = match_sublayers(auth, cap)
        assert r["ambiguous"] == [
            {"authoritative_names": ["Water_Valve", "water_valve"], "captured_names": ["water_valve"]}
        ]

    def test_match_is_case_insensitive(self):
        # AGOL name casing varies by org (PascalCase here); matching must
        # align a pair regardless of casing and carry the authoritative side's
        # verbatim name through as the pair's name.
        auth = [_sub("Water_Hydrants", url="A/0")]
        cap = [_sub("water_hydrants", url="C/0")]
        r = match_sublayers(auth, cap)
        assert [m["name"] for m in r["matched"]] == ["Water_Hydrants"]
        assert r["auth_only"] == []
        assert r["captured_only"] == []

    def test_matched_pair_carries_authoritative_verbatim_name(self):
        # When the two services use different casing for the same layer, the
        # authoritative side's verbatim name is the canonical pair name.
        auth = [_sub("Water_Hydrants", url="A/0")]
        cap = [_sub("WATER_HYDRANTS", url="C/0")]
        r = match_sublayers(auth, cap)
        assert r["matched"][0]["name"] == "Water_Hydrants"


class TestClassifyMatches:
    def test_point_pair_emitted(self):
        matched = [_match("a", "A/0", "C/0")]
        c = classify_matches(matched)
        assert [m["name"] for m in c["point"]] == ["a"]
        assert c["non_point"] == []
        assert c["geometry_mismatch"] == []
        assert c["unknown_geometry"] == []

    def test_non_point_agreeing_pair_skipped(self):
        matched = [_match("a", "A/0", "C/0", a_geom=POLY, c_geom=POLY)]
        c = classify_matches(matched)
        assert c["point"] == []
        assert [m["name"] for m in c["non_point"]] == ["a"]
        assert c["geometry_mismatch"] == []
        assert c["unknown_geometry"] == []

    def test_geometry_mismatch_skipped(self):
        matched = [_match("a", "A/0", "C/0", a_geom=POINT, c_geom=POLY)]
        c = classify_matches(matched)
        assert c["point"] == []
        assert c["non_point"] == []
        assert [m["name"] for m in c["geometry_mismatch"]] == ["a"]
        assert c["unknown_geometry"] == []

    def test_unknown_geometry_when_either_side_none(self):
        matched = [
            _match("auth_unknown", "A/0", "C/0", a_geom=None, c_geom=POINT),
            _match("cap_unknown", "A/1", "C/1", a_geom=POINT, c_geom=None),
            _match("both_unknown", "A/2", "C/2", a_geom=None, c_geom=None),
        ]
        c = classify_matches(matched)
        assert c["point"] == []
        assert sorted(m["name"] for m in c["unknown_geometry"]) == [
            "auth_unknown",
            "both_unknown",
            "cap_unknown",
        ]


class TestPartitionPointMatches:
    def _existing(self):
        return {
            "water_hydrants": {
                "authoritative_url": "A/0", "captured_url": "C/0",
                "match_threshold_m": 5.0, "field_map": {}, "copy_attachments": True,
            },
            "url_shift": {
                "authoritative_url": "A/99", "captured_url": "C/99",
                "match_threshold_m": 3.0, "field_map": {}, "copy_attachments": False,
            },
        }

    def test_no_existing_key_is_new(self):
        matches = [_match("new_layer", "A/5", "C/5")]
        p = partition_point_matches({}, matches)
        assert p["new"] == matches
        assert p["changed"] == []
        assert p["unchanged"] == []

    def test_existing_key_same_urls_is_unchanged(self):
        existing = self._existing()
        matches = [_match("water_hydrants", "A/0", "C/0")]
        p = partition_point_matches(existing, matches)
        assert p["new"] == []
        assert p["changed"] == []
        assert len(p["unchanged"]) == 1
        assert p["unchanged"][0]["_existing_key"] == "water_hydrants"
        assert p["unchanged"][0]["name"] == "water_hydrants"

    def test_existing_key_different_url_is_changed(self):
        existing = self._existing()
        matches = [_match("url_shift", "A/2", "C/2")]
        p = partition_point_matches(existing, matches)
        assert p["new"] == []
        assert p["unchanged"] == []
        assert len(p["changed"]) == 1
        assert p["changed"][0]["_existing_key"] == "url_shift"
        assert p["changed"][0]["authoritative_url"] == "A/2"

    def test_case_insensitive_lookup_preserves_existing_key(self):
        existing = self._existing()
        matches = [_match("Water_Hydrants", "A/0", "C/0")]
        p = partition_point_matches(existing, matches)
        assert p["unchanged"][0]["_existing_key"] == "water_hydrants"

    def test_new_match_carries_no_existing_key(self):
        matches = [_match("new_layer", "A/5", "C/5")]
        p = partition_point_matches({}, matches)
        assert "_existing_key" not in p["new"][0]

    def test_case_colliding_existing_keys_raise(self):
        existing = {
            "Water_Hydrants": self._existing()["water_hydrants"],
            "water_hydrants": self._existing()["water_hydrants"],
        }
        matches = [_match("Water_Hydrants", "A/9", "C/9")]
        with pytest.raises(ValueError, match="collide case-insensitively"):
            partition_point_matches(existing, matches)


class TestBuildLayerEntry:
    def test_correct_keys_and_insertion_order(self):
        entry = build_layer_entry(_match("a", "A/0", "C/0"), 10.67, True)
        # Insertion order must match config.json's existing convention so a
        # newly-added entry diffs cleanly next to refreshed ones.
        assert list(entry.keys()) == [
            "authoritative_url",
            "captured_url",
            "match_threshold_m",
            "field_map",
            "copy_attachments",
        ]

    def test_values_come_from_match_and_defaults(self):
        entry = build_layer_entry(_match("a", "A/0", "C/0"), 10.67, False)
        assert entry["authoritative_url"] == "A/0"
        assert entry["captured_url"] == "C/0"
        assert entry["match_threshold_m"] == 10.67
        assert entry["field_map"] == {}
        assert entry["copy_attachments"] is False

    def test_no_type_field_keys(self):
        entry = build_layer_entry(_match("a", "A/0", "C/0"), 10.67, True)
        assert "type_field_authoritative" not in entry
        assert "type_field_captured" not in entry

    def test_passes_validate_layer_config(self):
        entry = build_layer_entry(_match("a", "A/0", "C/0"), 10.67, True)
        # Must not raise.
        validate_layer_config(entry)


class TestMergeLayersConfig:
    def _existing(self):
        return {
            "water_hydrants": {
                "authoritative_url": "A/0",
                "captured_url": "C/0",
                "match_threshold_m": 5.0,
                "field_map": {},
                "copy_attachments": True,
            },
            "hand_kept": {
                "authoritative_url": "OLD/0",
                "captured_url": "OLDC/0",
                "match_threshold_m": 1.0,
                "field_map": {"a": "b"},
                "copy_attachments": False,
                "type_field_authoritative": "T",
                "type_field_captured": "T",
            },
            "url_shift": {
                "authoritative_url": "A/99",
                "captured_url": "C/99",
                "match_threshold_m": 3.0,
                "field_map": {},
                "copy_attachments": False,
            },
        }

    def test_added_new_key(self):
        existing = {"hand_kept": self._existing()["hand_kept"]}
        matches = [_match("new_layer", "A/5", "C/5")]
        r = merge_layers_config(existing, matches, 10.67, True)
        assert r["added"] == ["new_layer"]
        assert r["updated"] == []
        assert r["unchanged"] == []
        assert r["layers"]["new_layer"]["authoritative_url"] == "A/5"
        assert r["layers"]["new_layer"]["match_threshold_m"] == 10.67
        assert r["layers"]["hand_kept"] == existing["hand_kept"]  # untouched

    def test_unchanged_when_urls_already_equal(self):
        existing = self._existing()
        matches = [_match("water_hydrants", "A/0", "C/0")]
        r = merge_layers_config(existing, matches, 10.67, True)
        assert r["unchanged"] == ["water_hydrants"]
        assert r["added"] == []
        assert r["updated"] == []
        # Existing threshold/field_map/copy_attachments left as-is.
        assert r["layers"]["water_hydrants"]["match_threshold_m"] == 5.0

    def test_updated_refreshes_only_urls_records_old_new(self):
        existing = self._existing()
        matches = [_match("url_shift", "A/2", "C/2")]
        r = merge_layers_config(existing, matches, 10.67, True)
        assert r["updated"] == [
            {
                "name": "url_shift",
                "changes": {
                    "authoritative_url": ["A/99", "A/2"],
                    "captured_url": ["C/99", "C/2"],
                },
            }
        ]
        assert r["added"] == []
        assert r["unchanged"] == []
        refreshed = r["layers"]["url_shift"]
        assert refreshed["authoritative_url"] == "A/2"
        assert refreshed["captured_url"] == "C/2"
        # Every non-URL field is byte-for-byte untouched.
        assert refreshed["match_threshold_m"] == 3.0
        assert refreshed["field_map"] == {}
        assert refreshed["copy_attachments"] is False

    def test_updated_preserves_key_order_and_type_field_pair(self):
        existing = {
            "with_type": {
                "authoritative_url": "A/0",
                "captured_url": "C/0",
                "match_threshold_m": 1.0,
                "type_field_authoritative": "STRUCTTYPE",
                "type_field_captured": "STRUCTTYPE",
                "field_map": {},
                "copy_attachments": True,
            },
        }
        matches = [_match("with_type", "A/7", "C/7")]
        r = merge_layers_config(existing, matches, 10.67, True)
        # Key order preserved (URLs keep their original positions, not re-appended).
        assert list(r["layers"]["with_type"].keys()) == [
            "authoritative_url",
            "captured_url",
            "match_threshold_m",
            "type_field_authoritative",
            "type_field_captured",
            "field_map",
            "copy_attachments",
        ]
        assert r["layers"]["with_type"]["type_field_authoritative"] == "STRUCTTYPE"
        assert r["layers"]["with_type"]["type_field_captured"] == "STRUCTTYPE"

    def test_existing_non_matching_key_left_alone_in_no_bucket(self):
        existing = self._existing()
        matches = [_match("water_hydrants", "A/0", "C/0")]  # only water_hydrants matches
        r = merge_layers_config(existing, matches, 10.67, True)
        # hand_kept has no matching name -> left alone, in no bucket.
        assert "hand_kept" in r["layers"]
        assert r["layers"]["hand_kept"] == existing["hand_kept"]
        assert "hand_kept" not in r["added"]
        assert "hand_kept" not in r["updated"]
        assert "hand_kept" not in r["unchanged"]

    def test_added_does_not_mutate_input_existing(self):
        existing = {}
        matches = [_match("new_layer", "A/5", "C/5")]
        merge_layers_config(existing, matches, 10.67, True)
        assert existing == {}  # input not mutated

    def test_case_insensitive_match_keeps_existing_key_casing_when_unchanged(self):
        # Real-world shape: existing lowercase snake_case key, live PascalCase
        # name. Must match case-insensitively and keep the existing key.
        existing = {"water_hydrants": {
            "authoritative_url": "A/0", "captured_url": "C/0",
            "match_threshold_m": 5.0, "field_map": {}, "copy_attachments": True,
        }}
        matches = [_match("Water_Hydrants", "A/0", "C/0")]
        r = merge_layers_config(existing, matches, 10.67, True)
        assert r["unchanged"] == ["water_hydrants"]
        assert "water_hydrants" in r["layers"]  # existing key casing preserved
        assert "Water_Hydrants" not in r["layers"]  # no PascalCase duplicate added

    def test_case_insensitive_update_preserves_type_field_and_key_casing(self):
        existing = {"water_network_structures": {
            "authoritative_url": "A/4", "captured_url": "C/4",
            "match_threshold_m": 5.0,
            "type_field_authoritative": "STRUCTTYPE",
            "type_field_captured": "STRUCTTYPE",
            "field_map": {}, "copy_attachments": True,
        }}
        # Sublayer index shifted 4 -> 44.
        matches = [_match("Water_Network_Structures", "A/44", "C/44")]
        r = merge_layers_config(existing, matches, 10.67, True)
        assert r["updated"] == [{"name": "water_network_structures", "changes": {
            "authoritative_url": ["A/4", "A/44"], "captured_url": ["C/4", "C/44"]}}]
        layer = r["layers"]["water_network_structures"]
        assert layer["authoritative_url"] == "A/44"
        assert layer["type_field_authoritative"] == "STRUCTTYPE"  # preserved
        assert layer["type_field_captured"] == "STRUCTTYPE"  # preserved
        assert "Water_Network_Structures" not in r["layers"]  # no duplicate

    def test_case_colliding_existing_keys_raise(self):
        # Two existing config keys that only differ by case can't be routed
        # unambiguously by the case-insensitive lookup -- one would silently
        # stop receiving refreshes. Must fail loudly instead.
        existing = {
            "Water_Hydrants": self._existing()["water_hydrants"],
            "water_hydrants": self._existing()["water_hydrants"],
        }
        matches = [_match("Water_Hydrants", "A/9", "C/9")]
        with pytest.raises(ValueError, match="collide case-insensitively"):
            merge_layers_config(existing, matches, 10.67, True)

    def test_new_entry_added_under_verbatim_agol_name(self):
        # Genuinely-new layer: added under the verbatim AGOL name (PascalCase),
        # per the chosen convention.
        existing = {"water_hydrants": {
            "authoritative_url": "A/0", "captured_url": "C/0",
            "match_threshold_m": 5.0, "field_map": {}, "copy_attachments": True,
        }}
        matches = [_match("Water_Pumps", "A/2", "C/2")]
        r = merge_layers_config(existing, matches, 10.67, True)
        assert r["added"] == ["Water_Pumps"]
        assert "Water_Pumps" in r["layers"]
        assert r["layers"]["Water_Pumps"]["authoritative_url"] == "A/2"
        # Existing entry untouched.
        assert "water_hydrants" in r["layers"]


class TestMergeLayersConfigResolvedThreshold:
    """Calibration attaches an optional "resolved_threshold_m" onto a match
    dict (see cli._do_auto_configure); merge_layers_config must honor it when
    present and otherwise behave exactly as if it were never set (see the
    TestMergeLayersConfig class above, none of which sets this key)."""

    def _existing(self):
        return {
            "url_shift": {
                "authoritative_url": "A/99", "captured_url": "C/99",
                "match_threshold_m": 3.0, "field_map": {}, "copy_attachments": False,
            },
        }

    def test_new_entry_uses_resolved_threshold_over_default(self):
        match = {**_match("new_layer", "A/5", "C/5"), "resolved_threshold_m": 7.5}
        r = merge_layers_config({}, [match], 10.67, True)
        assert r["layers"]["new_layer"]["match_threshold_m"] == 7.5

    def test_new_entry_rounds_resolved_threshold_to_two_decimals(self):
        match = {**_match("new_layer", "A/5", "C/5"), "resolved_threshold_m": 7.34129871}
        r = merge_layers_config({}, [match], 10.67, True)
        assert r["layers"]["new_layer"]["match_threshold_m"] == 7.34

    def test_new_entry_without_resolved_threshold_uses_default(self):
        match = _match("new_layer", "A/5", "C/5")
        r = merge_layers_config({}, [match], 10.67, True)
        assert r["layers"]["new_layer"]["match_threshold_m"] == 10.67

    def test_changed_entry_with_resolved_threshold_updates_and_reports(self):
        existing = self._existing()
        match = {**_match("url_shift", "A/2", "C/2"), "resolved_threshold_m": 8.1}
        r = merge_layers_config(existing, [match], 10.67, True)
        assert r["layers"]["url_shift"]["match_threshold_m"] == 8.1
        assert r["updated"] == [
            {
                "name": "url_shift",
                "changes": {
                    "authoritative_url": ["A/99", "A/2"],
                    "captured_url": ["C/99", "C/2"],
                    "match_threshold_m": [3.0, 8.1],
                },
            }
        ]

    def test_changed_entry_resolved_threshold_equal_to_existing_not_reported(self):
        # Calibration landed on the same value already in config.json --
        # nothing to report, no spurious "changes" entry.
        existing = self._existing()
        match = {**_match("url_shift", "A/2", "C/2"), "resolved_threshold_m": 3.0}
        r = merge_layers_config(existing, [match], 10.67, True)
        assert r["layers"]["url_shift"]["match_threshold_m"] == 3.0
        assert "match_threshold_m" not in r["updated"][0]["changes"]

    def test_changed_entry_without_resolved_threshold_preserves_existing(self):
        # No calibration attempted (e.g. --no-calibrate-thresholds, or it
        # failed) -- must behave exactly like the pre-calibration code path:
        # only URLs refresh, threshold is untouched.
        existing = self._existing()
        match = _match("url_shift", "A/2", "C/2")
        r = merge_layers_config(existing, [match], 10.67, True)
        assert r["layers"]["url_shift"]["match_threshold_m"] == 3.0
        assert "match_threshold_m" not in r["updated"][0]["changes"]

    def test_unchanged_entry_ignores_resolved_threshold(self):
        # Even if something attached a resolved_threshold_m to an unchanged
        # match, it must never be applied -- unchanged layers are never
        # touched (this is what keeps a no-op re-run a true no-op).
        existing = {"water_hydrants": {
            "authoritative_url": "A/0", "captured_url": "C/0",
            "match_threshold_m": 5.0, "field_map": {}, "copy_attachments": True,
        }}
        match = {**_match("water_hydrants", "A/0", "C/0"), "resolved_threshold_m": 99.0}
        r = merge_layers_config(existing, [match], 10.67, True)
        assert r["layers"]["water_hydrants"]["match_threshold_m"] == 5.0
        assert r["unchanged"] == ["water_hydrants"]