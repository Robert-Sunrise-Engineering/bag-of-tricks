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
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

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
