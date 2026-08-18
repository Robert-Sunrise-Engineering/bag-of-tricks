"""Integration-level tests for conflate.cli.main()'s full apply pipeline.

tests/test_cli.py covers cli.py's pure helper functions in isolation.
tests/test_attachments.py and tests/test_cli.py::TestSeedClaimedOids cover
copy_attachments/_seed_claimed_oids as standalone units. Neither exercises
main()'s actual wiring: does a second run really refuse to re-claim an
authoritative record a prior run already matched, and does an attachment
copy failure really keep a feature off the ledger *and* get it retried?
These tests answer both by driving main() itself against a fake AGOL layer,
with no real network/file dependencies.
"""

import csv
import glob
import json
import logging
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import conflate.cli as cli

AUTH_URL = "https://example.com/arcgis/rest/services/Auth/FeatureServer/0"
CAPTURED_URL = "https://example.com/arcgis/rest/services/Captured/FeatureServer/0"


class _FakeFeature:
    def __init__(self, raw):
        self._raw = raw

    def as_dict(self):
        return self._raw


class FakeFeatureLayer:
    """Minimal stand-in for arcgis.features.FeatureLayer covering everything
    main()'s pipeline touches: .properties (validate_schema/capabilities/
    geometry_type), .query (fetch_all_features + its count cross-check),
    .edit_features (apply_updates/apply_appends), and .attachments
    (copy_attachments). Stateless across instances by design -- tests build
    a fresh instance per main() call rather than relying on one instance's
    edit_features mutations being visible to the next call, since a second
    real main() run against AGOL wouldn't share in-process state either.
    """

    def __init__(
        self,
        features,
        *,
        has_attachments=False,
        geometry_type="esriGeometryPoint",
        capabilities="Query,Create,Update,Delete",
    ):
        self.features = [dict(f) for f in features]
        self._next_oid = (
            max((f["attributes"].get("OBJECTID", 0) for f in self.features), default=0) + 1
        )
        field_names = {"OBJECTID", "GlobalID"}
        for f in self.features:
            field_names.update(f.get("attributes", {}).keys())
        props = {
            "hasAttachments": has_attachments,
            "capabilities": capabilities,
            "geometryType": geometry_type,
        }
        self.properties = SimpleNamespace(
            fields=[{"name": n} for n in field_names],
            get=props.get,
        )
        self.attachments = MagicMock()
        self.attachments.get_list.return_value = []

    def query(self, where=None, out_fields=None, return_all_records=None,
              out_sr=None, return_count_only=False):
        if return_count_only:
            return len(self.features)
        return SimpleNamespace(features=[_FakeFeature(f) for f in self.features])

    def edit_features(self, adds=None, updates=None, deletes=None):
        if adds is not None:
            results = []
            for a in adds:
                oid = self._next_oid
                self._next_oid += 1
                stored_attrs = dict(a["attributes"])
                stored_attrs["OBJECTID"] = oid
                self.features.append({"attributes": stored_attrs, "geometry": a.get("geometry")})
                results.append({"objectId": oid, "success": True})
            return {"addResults": results}
        if updates is not None:
            return {
                "updateResults": [
                    {"objectId": u["attributes"].get("OBJECTID"), "success": True}
                    for u in updates
                ]
            }
        if deletes is not None:
            self.features = [
                f for f in self.features if f["attributes"].get("OBJECTID") not in deletes
            ]
            return {"deleteResults": [{"objectId": oid, "success": True} for oid in deletes]}
        return {}


class _Clock:
    """Patched in for conflate.cli.datetime so tests control run_timestamp
    (and mark_processed's run_time) instead of racing real wall-clock
    seconds across back-to-back main() calls in the same test."""

    def __init__(self, start):
        self.current = start

    def now(self):
        return self.current


def _base_layer_cfg(**overrides):
    cfg = {
        "authoritative_url": AUTH_URL,
        "captured_url": CAPTURED_URL,
        "match_threshold_m": 10.0,
        "field_map": {},
        "copy_attachments": False,
    }
    cfg.update(overrides)
    return {"layers": {"testlayer": cfg}}


def _run_main(monkeypatch, tmp_path, *, config, auth_layer, captured_layer, clock, apply=True):
    layers_by_url = {AUTH_URL: auth_layer, CAPTURED_URL: captured_layer}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda path: config)
    monkeypatch.setattr(
        cli, "load_local_config", lambda path: {"portal_url": "https://example.com", "username": "u", "password": "p"}
    )
    monkeypatch.setattr(cli, "connect", lambda local_config: "fake-gis")
    monkeypatch.setattr(cli, "get_layer", lambda gis, url: layers_by_url[url])
    monkeypatch.setattr(cli, "datetime", clock)

    argv = ["conflate", "--layer", "testlayer"]
    if apply:
        argv.append("--apply")
    monkeypatch.setattr(sys, "argv", argv)

    cli.main()


def _ledger(tmp_path):
    path = tmp_path / "state" / "testlayer.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_report_rows(tmp_path):
    paths = sorted(glob.glob(str(tmp_path / "reports" / "testlayer_*.csv")))
    with open(paths[-1], newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class TestCrossRunMatchClaiming:
    """Reproduces the scenario _seed_claimed_oids guards against: a second
    run's captured feature landing within threshold of an authoritative
    record a *prior* run already matched and updated."""

    def test_second_run_does_not_reclaim_oid_matched_by_first_run(self, monkeypatch, tmp_path):
        auth_feature_5 = {
            "attributes": {"OBJECTID": 5, "GlobalID": "{AUTH-5}", "Notes": None},
            "geometry": {"x": -122.0, "y": 45.0},
        }
        captured_a = {
            "attributes": {"OBJECTID": 101, "GlobalID": "{CAP-A}", "Notes": "from A"},
            "geometry": {"x": -122.0, "y": 45.0},
        }
        captured_b = {
            "attributes": {"OBJECTID": 102, "GlobalID": "{CAP-B}", "Notes": "from B"},
            "geometry": {"x": -122.0, "y": 45.0},
        }

        config = _base_layer_cfg()
        clock = _Clock(datetime(2026, 1, 1, 10, 0, 0))

        # --- Run 1: only A is captured; matches and updates OID 5. ---
        _run_main(
            monkeypatch, tmp_path,
            config=config,
            auth_layer=FakeFeatureLayer([auth_feature_5]),
            captured_layer=FakeFeatureLayer([captured_a]),
            clock=clock,
        )

        ledger_after_run1 = _ledger(tmp_path)
        assert ledger_after_run1["{CAP-A}"]["authoritative_oid"] == 5
        assert ledger_after_run1["{CAP-A}"]["action"] == "updated"

        # --- Run 2: A reappears (already processed, skipped) plus new B,
        # also within threshold of OID 5, with no other authoritative
        # candidate. B must NOT re-claim OID 5. ---
        clock.current = datetime(2026, 1, 1, 10, 5, 0)
        _run_main(
            monkeypatch, tmp_path,
            config=config,
            auth_layer=FakeFeatureLayer([auth_feature_5]),
            captured_layer=FakeFeatureLayer([captured_a, captured_b]),
            clock=clock,
        )

        ledger_after_run2 = _ledger(tmp_path)
        assert ledger_after_run2["{CAP-A}"]["authoritative_oid"] == 5  # untouched
        assert ledger_after_run2["{CAP-B}"]["action"] == "created"
        assert ledger_after_run2["{CAP-B}"]["authoritative_oid"] != 5

        rows_by_gid = {r["captured_global_id"]: r for r in _latest_report_rows(tmp_path)}
        assert rows_by_gid["{CAP-B}"]["action"] == "appended"


class TestAttachmentFailureAsSuccess:
    """Reproduces the scenario _attachments_fully_succeeded/copy_attachments's
    None-vs-"0/0" distinction guards against, through main()'s actual
    ledgering decision -- not just the pure helper functions."""

    def _scenario_config(self):
        return _base_layer_cfg(copy_attachments=True)

    def _captured_layer(self, *, fail_for_oid):
        captured_x = {
            "attributes": {"OBJECTID": 201, "GlobalID": "{CAP-X}"},
            "geometry": {"x": -122.1, "y": 45.1},
        }
        captured_y = {
            "attributes": {"OBJECTID": 202, "GlobalID": "{CAP-Y}"},
            "geometry": {"x": -122.2, "y": 45.2},
        }
        layer = FakeFeatureLayer([captured_x, captured_y])

        def get_list_side_effect(oid):
            if oid == fail_for_oid:
                raise Exception("simulated attachment listing failure")
            return []

        layer.attachments.get_list = MagicMock(side_effect=get_list_side_effect)
        return layer

    def test_attachment_failure_keeps_feature_success_true_but_unledgered(
        self, monkeypatch, tmp_path
    ):
        config = self._scenario_config()
        clock = _Clock(datetime(2026, 1, 1, 10, 0, 0))

        _run_main(
            monkeypatch, tmp_path,
            config=config,
            auth_layer=FakeFeatureLayer([], has_attachments=True),  # no candidates -> both appended
            captured_layer=self._captured_layer(fail_for_oid=202),  # Y fails
            clock=clock,
        )

        rows_by_gid = {r["captured_global_id"]: r for r in _latest_report_rows(tmp_path)}
        assert rows_by_gid["{CAP-X}"]["success"] == "True"
        assert rows_by_gid["{CAP-X}"]["ledgered"] == "True"
        assert rows_by_gid["{CAP-X}"]["attachments_status"] == "0/0"

        assert rows_by_gid["{CAP-Y}"]["success"] == "True"
        assert rows_by_gid["{CAP-Y}"]["ledgered"] == "False"
        assert rows_by_gid["{CAP-Y}"]["attachments_status"] == ""

        ledger = _ledger(tmp_path)
        assert "{CAP-X}" in ledger
        assert "{CAP-Y}" not in ledger

    def test_unledgered_feature_is_retried_on_next_run(self, monkeypatch, tmp_path):
        config = self._scenario_config()
        clock = _Clock(datetime(2026, 1, 1, 10, 0, 0))

        _run_main(
            monkeypatch, tmp_path,
            config=config,
            auth_layer=FakeFeatureLayer([], has_attachments=True),
            captured_layer=self._captured_layer(fail_for_oid=202),
            clock=clock,
        )
        assert "{CAP-Y}" not in _ledger(tmp_path)

        # Run 2: Y's attachment listing now succeeds. X is already ledgered
        # (skipped via is_processed); Y must be reconsidered, not silently
        # dropped forever.
        clock.current = datetime(2026, 1, 1, 10, 5, 0)
        _run_main(
            monkeypatch, tmp_path,
            config=config,
            auth_layer=FakeFeatureLayer([], has_attachments=True),
            captured_layer=self._captured_layer(fail_for_oid=None),
            clock=clock,
        )

        ledger = _ledger(tmp_path)
        assert "{CAP-Y}" in ledger
        assert ledger["{CAP-Y}"]["action"] == "created"


class TestNewMatchColumns:
    """assign_matches()'s candidates_json/assignment_overridden_nearest must
    appear in BOTH the dry-run report and the apply/outcome report. The
    dry-run report is only ever written when --apply is not passed
    (cli.py's main() returns immediately after writing it), so the outcome
    report is the only persisted artifact for a live run -- if these columns
    were dry-run-only, an applied run would carry zero audit context for its
    own close calls."""

    def _auth_and_captured(self):
        auth_feature = {
            "attributes": {"OBJECTID": 5, "GlobalID": "{AUTH-5}"},
            "geometry": {"x": -122.0, "y": 45.0},
        }
        captured_match = {
            # same point as auth_feature -> distance 0, matched, single candidate
            "attributes": {"OBJECTID": 101, "GlobalID": "{CAP-MATCH}"},
            "geometry": {"x": -122.0, "y": 45.0},
        }
        captured_append = {
            # far from any authoritative feature -> no candidates, appended
            "attributes": {"OBJECTID": 102, "GlobalID": "{CAP-APPEND}"},
            "geometry": {"x": 10.0, "y": 10.0},
        }
        return auth_feature, captured_match, captured_append

    def test_dry_run_report_carries_new_columns(self, monkeypatch, tmp_path):
        auth_feature, captured_match, captured_append = self._auth_and_captured()
        captured_no_geom = {
            "attributes": {"OBJECTID": 103, "GlobalID": "{CAP-NOGEOM}"},
            "geometry": None,
        }

        config = _base_layer_cfg()
        clock = _Clock(datetime(2026, 1, 1, 10, 0, 0))

        _run_main(
            monkeypatch, tmp_path,
            config=config,
            auth_layer=FakeFeatureLayer([auth_feature]),
            captured_layer=FakeFeatureLayer([captured_match, captured_append, captured_no_geom]),
            clock=clock,
            apply=False,
        )

        rows_by_gid = {r["captured_global_id"]: r for r in _latest_report_rows(tmp_path)}

        matched_row = rows_by_gid["{CAP-MATCH}"]
        assert matched_row["action"] == "would_update"
        candidates = json.loads(matched_row["candidates_json"])
        assert len(candidates) == 1
        assert candidates[0]["OBJECTID"] == 5
        assert matched_row["assignment_overridden_nearest"] == "False"

        appended_row = rows_by_gid["{CAP-APPEND}"]
        assert appended_row["action"] == "would_append"
        assert json.loads(appended_row["candidates_json"]) == []
        assert appended_row["assignment_overridden_nearest"] == "False"

        no_geom_row = rows_by_gid["{CAP-NOGEOM}"]
        assert no_geom_row["action"] == "skipped_no_geometry"
        assert json.loads(no_geom_row["candidates_json"]) == []
        assert no_geom_row["assignment_overridden_nearest"] == "False"

    def test_outcome_report_carries_new_columns(self, monkeypatch, tmp_path):
        auth_feature, captured_match, captured_append = self._auth_and_captured()

        config = _base_layer_cfg()
        clock = _Clock(datetime(2026, 1, 1, 10, 0, 0))

        _run_main(
            monkeypatch, tmp_path,
            config=config,
            auth_layer=FakeFeatureLayer([auth_feature]),
            captured_layer=FakeFeatureLayer([captured_match, captured_append]),
            clock=clock,
            apply=True,
        )

        rows_by_gid = {r["captured_global_id"]: r for r in _latest_report_rows(tmp_path)}

        matched_row = rows_by_gid["{CAP-MATCH}"]
        assert matched_row["action"] == "updated"
        candidates = json.loads(matched_row["candidates_json"])
        assert len(candidates) == 1
        assert candidates[0]["OBJECTID"] == 5
        assert matched_row["assignment_overridden_nearest"] == "False"

        appended_row = rows_by_gid["{CAP-APPEND}"]
        assert appended_row["action"] == "appended"
        assert json.loads(appended_row["candidates_json"]) == []


# ---------------------------------------------------------------------------
# --auto-configure end-to-end tests
# ---------------------------------------------------------------------------

AUTH_SERVICE_URL = "https://example.com/arcgis/rest/services/Auth/FeatureServer"
CAP_SERVICE_URL = "https://example.com/arcgis/rest/services/Captured/FeatureServer"


def _sub(name, layer_id, geometry_type="esriGeometryPoint", *, side="A"):
    """A sublayer dict in the shape list_service_sublayers returns. The url is
    built from the side + id so auth/captured URLs are distinguishable."""
    base = AUTH_SERVICE_URL if side == "A" else CAP_SERVICE_URL
    return {"id": layer_id, "name": name, "geometry_type": geometry_type, "url": f"{base}/{layer_id}"}


def _cal_feature(oid, lon, lat, **attrs):
    """A raw AGOL feature dict for calibration tests -- point geometry plus
    whatever extra attributes (e.g. a type field) the test needs."""
    return {"attributes": {"OBJECTID": oid, "GlobalID": f"g{oid}", **attrs}, "geometry": {"x": lon, "y": lat}}


def _run_auto_configure(
    monkeypatch, tmp_path, *, auth_sublayers, cap_sublayers, existing_config,
    threshold="10.67", no_copy_attachments=False, get_layer=None,
    no_calibrate_thresholds=True,
):
    """Drive cli.main() through the --auto-configure path with fakes for every
    AGOL touch point. Returns the dict captured by the patched save_config.

    Calibration is off by default here (no_calibrate_thresholds=True) so
    every pre-existing test in this module -- none of which fakes
    fetch_all_features or a real-shaped get_layer -- keeps exercising
    exactly the matching/merge behavior it was written for, undisturbed by
    the calibration feature. Tests of calibration itself pass
    no_calibrate_thresholds=False explicitly (see TestAutoConfigureCalibration)
    and supply the extra fakes that path needs.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli, "load_local_config",
        lambda path: {"portal_url": "https://example.com", "username": "u", "password": "p"},
    )
    monkeypatch.setattr(cli, "connect", lambda local_config: "fake-gis")

    sublayers_by_url = {AUTH_SERVICE_URL: auth_sublayers, CAP_SERVICE_URL: cap_sublayers}
    monkeypatch.setattr(cli, "list_service_sublayers", lambda gis, url: sublayers_by_url[url])

    monkeypatch.setattr(cli, "load_config", lambda path: existing_config)

    saved = {}

    def fake_save(path, config):
        saved["path"] = path
        saved["config"] = config

    monkeypatch.setattr(cli, "save_config", fake_save)

    if get_layer is not None:
        monkeypatch.setattr(cli, "get_layer", get_layer)

    argv = ["conflate", "--auto-configure", AUTH_SERVICE_URL, CAP_SERVICE_URL,
            "--default-threshold-m", threshold]
    if no_copy_attachments:
        argv.append("--no-copy-attachments")
    if no_calibrate_thresholds:
        argv.append("--no-calibrate-thresholds")
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    return saved


class TestAutoConfigure:
    def test_adds_new_matching_point_layers(self, monkeypatch, tmp_path, capsys):
        auth = [_sub("water_hydrants", 0), _sub("water_valves", 1)]
        cap = [_sub("water_hydrants", 0, side="C"), _sub("water_valves", 1, side="C")]
        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap, existing_config={"layers": {}},
        )
        layers = saved["config"]["layers"]
        assert set(layers) == {"water_hydrants", "water_valves"}
        assert layers["water_hydrants"]["authoritative_url"] == f"{AUTH_SERVICE_URL}/0"
        assert layers["water_hydrants"]["captured_url"] == f"{CAP_SERVICE_URL}/0"
        assert layers["water_hydrants"]["match_threshold_m"] == 10.67
        assert layers["water_hydrants"]["copy_attachments"] is True
        # Summary printed.
        out = capsys.readouterr().out
        assert "Added (2)" in out

    def test_no_copy_attachments_flag_sets_false_on_new_entries(self, monkeypatch, tmp_path):
        auth = [_sub("water_hydrants", 0)]
        cap = [_sub("water_hydrants", 0, side="C")]
        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap,
            existing_config={"layers": {}}, no_copy_attachments=True,
        )
        assert saved["config"]["layers"]["water_hydrants"]["copy_attachments"] is False

    def test_unchanged_when_urls_already_match(self, monkeypatch, tmp_path, capsys):
        auth = [_sub("water_hydrants", 0)]
        cap = [_sub("water_hydrants", 0, side="C")]
        existing = {"layers": {"water_hydrants": {
            "authoritative_url": f"{AUTH_SERVICE_URL}/0",
            "captured_url": f"{CAP_SERVICE_URL}/0",
            "match_threshold_m": 5.0, "field_map": {}, "copy_attachments": False,
        }}}
        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap, existing_config=existing,
        )
        # Unchanged: existing entry preserved verbatim (threshold stays 5.0, copy_attachments False).
        assert saved["config"]["layers"]["water_hydrants"]["match_threshold_m"] == 5.0
        assert saved["config"]["layers"]["water_hydrants"]["copy_attachments"] is False
        out = capsys.readouterr().out
        assert "Unchanged (1)" in out
        assert "Added" not in out

    def test_updates_shifted_sublayer_index(self, monkeypatch, tmp_path, capsys):
        # The live service now exposes water_hydrants at index 3 (was 0).
        auth = [_sub("water_hydrants", 3)]
        cap = [_sub("water_hydrants", 3, side="C")]
        existing = {"layers": {"water_hydrants": {
            "authoritative_url": f"{AUTH_SERVICE_URL}/0",
            "captured_url": f"{CAP_SERVICE_URL}/0",
            "match_threshold_m": 5.0, "field_map": {}, "copy_attachments": False,
        }}}
        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap, existing_config=existing,
        )
        layer = saved["config"]["layers"]["water_hydrants"]
        assert layer["authoritative_url"] == f"{AUTH_SERVICE_URL}/3"
        assert layer["captured_url"] == f"{CAP_SERVICE_URL}/3"
        # Non-URL fields untouched.
        assert layer["match_threshold_m"] == 5.0
        assert layer["copy_attachments"] is False
        out = capsys.readouterr().out
        assert "Updated (1)" in out
        assert "water_hydrants" in out

    def test_skips_non_point_and_geometry_mismatch(self, monkeypatch, tmp_path, capsys):
        auth = [
            _sub("point_lyr", 0),
            _sub("polygon_lyr", 1, geometry_type="esriGeometryPolygon"),
            _sub("mismatch_lyr", 2, geometry_type="esriGeometryPoint"),
        ]
        cap = [
            _sub("point_lyr", 0, side="C"),
            _sub("polygon_lyr", 1, side="C", geometry_type="esriGeometryPolygon"),
            _sub("mismatch_lyr", 2, side="C", geometry_type="esriGeometryPolyline"),
        ]
        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap, existing_config={"layers": {}},
        )
        layers = saved["config"]["layers"]
        assert set(layers) == {"point_lyr"}  # only the point-on-point pair emitted
        out = capsys.readouterr().out
        assert "Skipped (non-point geometry)" in out
        assert "polygon_lyr" in out
        assert "Skipped (geometry mismatch)" in out
        assert "mismatch_lyr" in out

    def test_unknown_geometry_resolved_via_get_layer(self, monkeypatch, tmp_path, capsys):
        # Root summary omitted geometryType for both sides; a direct get_layer
        # lookup resolves them to point.
        auth = [_sub("unknown_lyr", 0, geometry_type=None)]
        cap = [_sub("unknown_lyr", 0, side="C", geometry_type=None)]

        class _ResolvedLayer:
            def __init__(self, gis, url):
                self.properties = {"geometryType": "esriGeometryPoint"}

        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap,
            existing_config={"layers": {}}, get_layer=_ResolvedLayer,
        )
        assert "unknown_lyr" in saved["config"]["layers"]
        out = capsys.readouterr().out
        assert "Added (1)" in out

    def test_unmatched_sides_reported(self, monkeypatch, tmp_path, capsys):
        auth = [_sub("only_in_auth", 0)]
        cap = [_sub("only_in_cap", 0, side="C")]
        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap, existing_config={"layers": {}},
        )
        assert saved["config"]["layers"] == {}
        out = capsys.readouterr().out
        assert "Unmatched (authoritative only)" in out
        assert "only_in_auth" in out
        assert "Unmatched (captured only)" in out
        assert "only_in_cap" in out

    def test_case_insensitive_match_preserves_existing_lowercase_keys(self, monkeypatch, tmp_path, capsys):
        # Real-world shape: live services use PascalCase sublayer names while
        # existing config keys are lowercase snake_case, some with hand-curated
        # type_field pairs. Must refresh in place (preserving type_field + key
        # casing) and add only genuinely-new layers under their verbatim name.
        auth = [
            _sub("Water_Hydrants", 6),
            _sub("Water_Network_Structures", 4),
            _sub("Water_Pumps", 2),  # genuinely new
        ]
        cap = [
            _sub("Water_Hydrants", 6, side="C"),
            _sub("Water_Network_Structures", 4, side="C"),
            _sub("Water_Pumps", 2, side="C"),
        ]
        existing = {
            "layers": {
                "water_hydrants": {
                    "authoritative_url": f"{AUTH_SERVICE_URL}/6",
                    "captured_url": f"{CAP_SERVICE_URL}/6",
                    "match_threshold_m": 5.0, "field_map": {}, "copy_attachments": True,
                },
                "water_network_structures": {
                    "authoritative_url": f"{AUTH_SERVICE_URL}/4",
                    "captured_url": f"{CAP_SERVICE_URL}/4",
                    "match_threshold_m": 5.0,
                    "type_field_authoritative": "STRUCTTYPE",
                    "type_field_captured": "STRUCTTYPE",
                    "field_map": {}, "copy_attachments": True,
                },
            }
        }
        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap, existing_config=existing,
        )
        layers = saved["config"]["layers"]
        # Existing keys preserved (lowercase), type_field pair intact.
        assert "water_hydrants" in layers
        assert "water_network_structures" in layers
        assert layers["water_network_structures"]["type_field_authoritative"] == "STRUCTTYPE"
        assert layers["water_network_structures"]["type_field_captured"] == "STRUCTTYPE"
        # No PascalCase duplicates of existing layers.
        assert "Water_Hydrants" not in layers
        assert "Water_Network_Structures" not in layers
        # Genuinely-new layer added under verbatim AGOL name.
        assert "Water_Pumps" in layers
        assert layers["Water_Pumps"]["authoritative_url"] == f"{AUTH_SERVICE_URL}/2"
        out = capsys.readouterr().out
        assert "Unchanged (2)" in out
        assert "Added (1)" in out
        assert "Water_Pumps" in out

    def test_unknown_geometry_resolution_failure_does_not_abort(self, monkeypatch, tmp_path, capsys):
        # A sublayer whose geometry type the root summary omitted, AND whose
        # per-sublayer lookup then fails (network/404), must not abort the
        # whole --auto-configure run -- the pair is skipped and the run
        # completes, per the "one bad layer never aborts" contract.
        auth = [_sub("water_hydrants", 0, geometry_type=None)]
        cap = [_sub("water_hydrants", 0, geometry_type=None, side="C")]

        def fake_get_layer(gis, url):
            raise RuntimeError("simulated lookup failure")

        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap,
            existing_config={"layers": {}}, get_layer=fake_get_layer,
            no_calibrate_thresholds=True,
        )
        # Run completed (no exception); the unresolvable pair was not emitted.
        assert saved["config"]["layers"] == {}
        out = capsys.readouterr().out
        assert "water_hydrants" in out


class TestAutoConfigureCalibration:
    """--auto-configure's default (--no-calibrate-thresholds not passed)
    per-layer threshold calibration path. calibrate.py's own math is covered
    by tests/test_calibrate.py; these tests are about cli.py's wiring:
    which layers get fetched, what happens on failure, and that a resolved
    threshold actually lands in the written config.
    """

    def test_new_layer_uses_calibrated_threshold(self, monkeypatch, tmp_path, capsys):
        auth = [_sub("water_hydrants", 0)]
        cap = [_sub("water_hydrants", 0, side="C")]

        auth_layer = FakeFeatureLayer([_cal_feature(1, 0.0, 0.0)])
        cap_layer = FakeFeatureLayer([_cal_feature(1, 0.0001, 0.0001)])
        layers_by_url = {f"{AUTH_SERVICE_URL}/0": auth_layer, f"{CAP_SERVICE_URL}/0": cap_layer}

        monkeypatch.setattr(
            cli, "suggest_threshold",
            lambda distances: {
                "suggested_threshold_m": 4.5, "confidence": "clear_bimodal_valley",
                "n_samples": 1, "fraction_within_suggested": 1.0, "distance_summary": {},
            },
        )
        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap,
            existing_config={"layers": {}}, get_layer=lambda gis, url: layers_by_url[url],
            no_calibrate_thresholds=False,
        )
        assert saved["config"]["layers"]["water_hydrants"]["match_threshold_m"] == 4.5
        out = capsys.readouterr().out
        assert "Threshold calibrated (1)" in out
        assert "water_hydrants: 4.50 m (clear_bimodal_valley)" in out

    def test_unchanged_layer_skips_calibration_fetch(self, monkeypatch, tmp_path, capsys):
        auth = [_sub("water_hydrants", 0)]
        cap = [_sub("water_hydrants", 0, side="C")]
        existing = {"layers": {"water_hydrants": {
            "authoritative_url": f"{AUTH_SERVICE_URL}/0",
            "captured_url": f"{CAP_SERVICE_URL}/0",
            "match_threshold_m": 5.0, "field_map": {}, "copy_attachments": True,
        }}}
        calls = []

        def fake_get_layer(gis, url):
            calls.append(url)
            return FakeFeatureLayer([])

        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap,
            existing_config=existing, get_layer=fake_get_layer, no_calibrate_thresholds=False,
        )
        # An unchanged layer is never fetched for calibration -- this is
        # what keeps a no-op re-run a true no-op.
        assert calls == []
        assert saved["config"]["layers"]["water_hydrants"]["match_threshold_m"] == 5.0
        out = capsys.readouterr().out
        assert "Threshold calibrated" not in out
        assert "Threshold fallback" not in out

    def test_calibration_exception_falls_back_to_default_threshold(self, monkeypatch, tmp_path, capsys):
        auth = [_sub("water_hydrants", 0)]
        cap = [_sub("water_hydrants", 0, side="C")]

        def fake_get_layer(gis, url):
            raise RuntimeError("simulated network failure")

        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap,
            existing_config={"layers": {}}, get_layer=fake_get_layer,
            no_calibrate_thresholds=False, threshold="12.5",
        )
        # The run completes and the layer is still added -- one bad layer
        # must not abort the whole --auto-configure run.
        assert saved["config"]["layers"]["water_hydrants"]["match_threshold_m"] == 12.5
        out = capsys.readouterr().out
        assert "Threshold fallback to --default-threshold-m (1)" in out
        assert "water_hydrants" in out

    def test_insufficient_data_falls_back_to_default_threshold(self, monkeypatch, tmp_path, capsys):
        auth = [_sub("water_hydrants", 0)]
        cap = [_sub("water_hydrants", 0, side="C")]
        auth_layer = FakeFeatureLayer([_cal_feature(1, 0.0, 0.0)])
        cap_layer = FakeFeatureLayer([_cal_feature(1, 0.0001, 0.0001)])
        layers_by_url = {f"{AUTH_SERVICE_URL}/0": auth_layer, f"{CAP_SERVICE_URL}/0": cap_layer}

        monkeypatch.setattr(
            cli, "suggest_threshold",
            lambda distances: {
                "suggested_threshold_m": None, "confidence": "insufficient_data",
                "n_samples": 1, "fraction_within_suggested": None, "distance_summary": None,
            },
        )
        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap,
            existing_config={"layers": {}}, get_layer=lambda gis, url: layers_by_url[url],
            no_calibrate_thresholds=False,
        )
        assert saved["config"]["layers"]["water_hydrants"]["match_threshold_m"] == 10.67
        out = capsys.readouterr().out
        assert "Threshold fallback to --default-threshold-m (1)" in out

    def test_non_positive_suggestion_falls_back_to_default_threshold(self, monkeypatch, tmp_path, capsys):
        # A calibrated value that rounds to <= 0 must never reach
        # match_threshold_m -- it would collapse assign_matches's cost
        # matrix (k=0) and silently accept arbitrary matches later.
        auth = [_sub("water_hydrants", 0)]
        cap = [_sub("water_hydrants", 0, side="C")]
        auth_layer = FakeFeatureLayer([_cal_feature(1, 0.0, 0.0)])
        cap_layer = FakeFeatureLayer([_cal_feature(1, 0.0001, 0.0001)])
        layers_by_url = {f"{AUTH_SERVICE_URL}/0": auth_layer, f"{CAP_SERVICE_URL}/0": cap_layer}

        monkeypatch.setattr(
            cli, "suggest_threshold",
            lambda distances: {
                "suggested_threshold_m": 0.001, "confidence": "low_no_clear_bimodal_separation",
                "n_samples": 1, "fraction_within_suggested": 1.0, "distance_summary": {},
            },
        )
        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap,
            existing_config={"layers": {}}, get_layer=lambda gis, url: layers_by_url[url],
            no_calibrate_thresholds=False, threshold="9.5",
        )
        assert saved["config"]["layers"]["water_hydrants"]["match_threshold_m"] == 9.5
        out = capsys.readouterr().out
        assert "Threshold fallback to --default-threshold-m (1)" in out

    def test_changed_layer_passes_existing_type_fields_to_nearest_distances(self, monkeypatch, tmp_path):
        # water_hydrants' sublayer index shifted 0 -> 3 (a URL refresh);
        # its hand-configured type_field pair must be reused for calibration.
        auth = [_sub("water_hydrants", 3)]
        cap = [_sub("water_hydrants", 3, side="C")]
        existing = {"layers": {"water_hydrants": {
            "authoritative_url": f"{AUTH_SERVICE_URL}/0",
            "captured_url": f"{CAP_SERVICE_URL}/0",
            "match_threshold_m": 5.0,
            "type_field_authoritative": "STRUCTTYPE",
            "type_field_captured": "STRUCTTYPE",
            "field_map": {}, "copy_attachments": True,
        }}}
        auth_layer = FakeFeatureLayer([_cal_feature(1, 0.0, 0.0, STRUCTTYPE="hydrant")])
        cap_layer = FakeFeatureLayer([_cal_feature(1, 0.0001, 0.0001, STRUCTTYPE="hydrant")])
        layers_by_url = {f"{AUTH_SERVICE_URL}/3": auth_layer, f"{CAP_SERVICE_URL}/3": cap_layer}

        real_nearest_distances = cli.nearest_distances
        captured_call = {}

        def spy_nearest_distances(captured_features, authoritative_features, tfa, tfc):
            captured_call["type_field_authoritative"] = tfa
            captured_call["type_field_captured"] = tfc
            return real_nearest_distances(captured_features, authoritative_features, tfa, tfc)

        monkeypatch.setattr(cli, "nearest_distances", spy_nearest_distances)

        _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap,
            existing_config=existing, get_layer=lambda gis, url: layers_by_url[url],
            no_calibrate_thresholds=False,
        )
        assert captured_call["type_field_authoritative"] == "STRUCTTYPE"
        assert captured_call["type_field_captured"] == "STRUCTTYPE"

    def test_changed_layer_schema_shift_falls_back_and_still_refreshes_urls(
        self, monkeypatch, tmp_path, capsys,
    ):
        # The URL changed AND the type field no longer exists on the new
        # service -- validate_schema must catch this before nearest_distances
        # ever runs a raw dict subscript on a missing key.
        auth = [_sub("water_hydrants", 3)]
        cap = [_sub("water_hydrants", 3, side="C")]
        existing = {"layers": {"water_hydrants": {
            "authoritative_url": f"{AUTH_SERVICE_URL}/0",
            "captured_url": f"{CAP_SERVICE_URL}/0",
            "match_threshold_m": 5.0,
            "type_field_authoritative": "STRUCTTYPE",
            "type_field_captured": "STRUCTTYPE",
            "field_map": {}, "copy_attachments": True,
        }}}
        # Neither fake feature carries STRUCTTYPE.
        auth_layer = FakeFeatureLayer([_cal_feature(1, 0.0, 0.0)])
        cap_layer = FakeFeatureLayer([_cal_feature(1, 0.0001, 0.0001)])
        layers_by_url = {f"{AUTH_SERVICE_URL}/3": auth_layer, f"{CAP_SERVICE_URL}/3": cap_layer}

        saved = _run_auto_configure(
            monkeypatch, tmp_path, auth_sublayers=auth, cap_sublayers=cap,
            existing_config=existing, get_layer=lambda gis, url: layers_by_url[url],
            no_calibrate_thresholds=False, threshold="12.5",
        )
        layer = saved["config"]["layers"]["water_hydrants"]
        assert layer["authoritative_url"] == f"{AUTH_SERVICE_URL}/3"
        # No resolved_threshold_m was attached (calibration never ran), so
        # the existing threshold is preserved exactly as a plain URL refresh
        # would leave it.
        assert layer["match_threshold_m"] == 5.0
        out = capsys.readouterr().out
        # A changed (URL-refreshed) layer whose calibration fails keeps its
        # existing threshold -- the summary must say so, not claim a fallback
        # to --default-threshold-m (which only applies to genuinely-new layers).
        assert "existing match_threshold_m preserved (1)" in out
        assert "Threshold fallback to --default-threshold-m" not in out


AUTH_URL_CAL = "https://example.com/arcgis/rest/services/Auth/FeatureServer/0"
CAPTURED_URL_CAL = "https://example.com/arcgis/rest/services/Captured/FeatureServer/0"


class TestCalibrateStandalone:
    """The standalone --calibrate mode: recalibrate one already-configured
    layer, writing back into config.json only with --apply.
    """

    def _run(self, monkeypatch, tmp_path, *, layer_cfg, auth_features, cap_features, apply=False, get_layer=None):
        monkeypatch.chdir(tmp_path)
        config = {"layers": {"water_hydrants": layer_cfg}}
        monkeypatch.setattr(
            cli, "load_local_config",
            lambda path: {"portal_url": "https://example.com", "username": "u", "password": "p"},
        )
        monkeypatch.setattr(cli, "connect", lambda local_config: "fake-gis")
        monkeypatch.setattr(cli, "load_config", lambda path: config)

        auth_layer = FakeFeatureLayer(auth_features)
        cap_layer = FakeFeatureLayer(cap_features)
        layers_by_url = {
            layer_cfg["authoritative_url"]: auth_layer,
            layer_cfg["captured_url"]: cap_layer,
        }
        if get_layer is None:
            get_layer = lambda gis, url: layers_by_url[url]
        monkeypatch.setattr(cli, "get_layer", get_layer)

        saved = {}

        def fake_save(path, cfg):
            saved["path"] = path
            saved["config"] = cfg

        monkeypatch.setattr(cli, "save_config", fake_save)

        argv = ["conflate", "--calibrate", "--layer", "water_hydrants"]
        if apply:
            argv.append("--apply")
        monkeypatch.setattr(sys, "argv", argv)
        cli.main()
        return saved

    def _layer_cfg(self, **overrides):
        cfg = {
            "authoritative_url": AUTH_URL_CAL, "captured_url": CAPTURED_URL_CAL,
            "match_threshold_m": 5.0, "field_map": {}, "copy_attachments": True,
        }
        cfg.update(overrides)
        return cfg

    def test_dry_run_does_not_write_config(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            cli, "suggest_threshold",
            lambda distances: {
                "suggested_threshold_m": 6.25, "confidence": "clear_bimodal_valley",
                "n_samples": 1, "fraction_within_suggested": 1.0, "distance_summary": {},
            },
        )
        saved = self._run(
            monkeypatch, tmp_path, layer_cfg=self._layer_cfg(),
            auth_features=[_cal_feature(1, 0.0, 0.0)],
            cap_features=[_cal_feature(1, 0.0001, 0.0001)],
            apply=False,
        )
        assert saved == {}

    def test_apply_writes_only_that_layers_threshold(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            cli, "suggest_threshold",
            lambda distances: {
                "suggested_threshold_m": 6.2543, "confidence": "clear_bimodal_valley",
                "n_samples": 1, "fraction_within_suggested": 1.0, "distance_summary": {},
            },
        )
        saved = self._run(
            monkeypatch, tmp_path, layer_cfg=self._layer_cfg(),
            auth_features=[_cal_feature(1, 0.0, 0.0)],
            cap_features=[_cal_feature(1, 0.0001, 0.0001)],
            apply=True,
        )
        layer = saved["config"]["layers"]["water_hydrants"]
        assert layer["match_threshold_m"] == 6.25  # rounded to 2 decimals
        # Every other field untouched.
        assert layer["authoritative_url"] == AUTH_URL_CAL
        assert layer["captured_url"] == CAPTURED_URL_CAL
        assert layer["field_map"] == {}
        assert layer["copy_attachments"] is True

    def test_insufficient_data_does_not_write_even_with_apply(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            cli, "suggest_threshold",
            lambda distances: {
                "suggested_threshold_m": None, "confidence": "insufficient_data",
                "n_samples": 0, "fraction_within_suggested": None, "distance_summary": None,
            },
        )
        saved = self._run(
            monkeypatch, tmp_path, layer_cfg=self._layer_cfg(),
            auth_features=[], cap_features=[], apply=True,
        )
        assert saved == {}

    def test_non_positive_suggestion_does_not_write_even_with_apply(self, monkeypatch, tmp_path):
        # Same non-positive-threshold guard as --auto-configure: a rounded
        # suggestion of <= 0 must never be written, even with --apply.
        monkeypatch.setattr(
            cli, "suggest_threshold",
            lambda distances: {
                "suggested_threshold_m": 0.001, "confidence": "low_no_clear_bimodal_separation",
                "n_samples": 1, "fraction_within_suggested": 1.0, "distance_summary": {},
            },
        )
        saved = self._run(
            monkeypatch, tmp_path, layer_cfg=self._layer_cfg(),
            auth_features=[_cal_feature(1, 0.0, 0.0)],
            cap_features=[_cal_feature(1, 0.0001, 0.0001)],
            apply=True,
        )
        assert saved == {}

    def test_calibration_failure_logs_and_does_not_write(self, monkeypatch, tmp_path, caplog):
        # A schema/network/fetch failure in _calibrate_layer_pair must surface
        # as a clean error and leave config.json untouched, not a raw
        # traceback -- the same robustness contract --auto-configure upholds.
        def failing_get_layer(gis, url):
            raise RuntimeError("simulated network failure")

        with caplog.at_level(logging.ERROR, logger="conflate.cli"):
            saved = self._run(
                monkeypatch, tmp_path, layer_cfg=self._layer_cfg(),
                auth_features=[_cal_feature(1, 0.0, 0.0)],
                cap_features=[_cal_feature(1, 0.0001, 0.0001)],
                apply=True, get_layer=failing_get_layer,
            )
        assert saved == {}
        assert "Calibration failed for layer 'water_hydrants'" in caplog.text
        assert "config.json not changed" in caplog.text


class TestAutoConfigureArgparseValidation:
    """argparse-level validation in main() must reject impossible flag combos."""

    def _run_argv(self, monkeypatch, argv):
        monkeypatch.setattr(sys, "argv", ["conflate"] + argv)
        with pytest.raises(SystemExit) as exc:
            cli.main()
        return exc.value.code

    def test_missing_threshold_errors(self, monkeypatch):
        code = self._run_argv(monkeypatch, ["--auto-configure", "A", "C"])
        assert code == 2

    def test_layer_with_auto_configure_errors(self, monkeypatch):
        code = self._run_argv(
            monkeypatch,
            ["--layer", "foo", "--auto-configure", "A", "C", "--default-threshold-m", "10.67"],
        )
        assert code == 2

    def test_negative_threshold_errors(self, monkeypatch):
        code = self._run_argv(
            monkeypatch, ["--auto-configure", "A", "C", "--default-threshold-m", "-5"],
        )
        assert code == 2

    def test_nan_threshold_errors(self, monkeypatch):
        # NaN compares False against <= 0, so without an isfinite guard it
        # would bypass the positivity check and land in config.json as a
        # literal NaN token (distance <= NaN is always False -> nothing
        # matches).
        code = self._run_argv(
            monkeypatch, ["--auto-configure", "A", "C", "--default-threshold-m", "nan"],
        )
        assert code == 2

    def test_inf_threshold_errors(self, monkeypatch):
        # inf likewise bypasses <= 0 and would make distance <= inf always
        # True -- every captured feature matching every authoritative one.
        code = self._run_argv(
            monkeypatch, ["--auto-configure", "A", "C", "--default-threshold-m", "inf"],
        )
        assert code == 2

    def test_auto_configure_with_rollback_errors(self, monkeypatch):
        code = self._run_argv(
            monkeypatch,
            ["--auto-configure", "A", "C", "--default-threshold-m", "10.67", "--rollback", "b.json"],
        )
        assert code == 2

    def test_no_layer_and_no_mode_errors(self, monkeypatch):
        code = self._run_argv(monkeypatch, [])
        assert code == 2

    def test_calibrate_without_layer_errors(self, monkeypatch):
        code = self._run_argv(monkeypatch, ["--calibrate"])
        assert code == 2

    def test_calibrate_with_rollback_errors(self, monkeypatch):
        code = self._run_argv(monkeypatch, ["--calibrate", "--layer", "foo", "--rollback", "b.json"])
        assert code == 2

    def test_calibrate_with_auto_configure_errors(self, monkeypatch):
        code = self._run_argv(
            monkeypatch,
            ["--auto-configure", "A", "C", "--default-threshold-m", "10.67", "--calibrate"],
        )
        assert code == 2
