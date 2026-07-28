"""Tests for conflate.rollback, in particular the --layer mismatch guard.

This guard exists because of a real incident: running --rollback with a
--layer that didn't match the layer a backup/report were generated for
caused deletes to hit an unrelated, already-populated authoritative layer.
"""

import csv
import json
from types import SimpleNamespace

import pytest

from conflate.backup import write_backup
from conflate.ledger import load_ledger
from conflate.rollback import rollback, LayerMismatchError


class _FakeFeature:
    def __init__(self, raw):
        self._raw = raw

    def as_dict(self):
        return self._raw


class FakeLayer:
    """Records edit_features/query calls; fails the test if called when it shouldn't be."""

    def __init__(self, query_returns=None, restore_verify_features=None,
                 raise_on_verify_query=False):
        self.calls = []
        self.query_calls = []
        # Values returned by successive return_count_only=True query() calls
        # (first call = "before" count, second = "after" count); popped in
        # order. Once exhausted, falls back to 0. Defaults to [] so a test
        # that never sets this up sees 0 for both before/after (may produce
        # a benign mismatch warning for tests that delete features, since a
        # fake layer with a fixed count doesn't simulate the deletion
        # actually happening -- that's fine, it's a warning, not a failure).
        self._query_returns = list(query_returns) if query_returns else []
        # Features returned by verify_restore's attribute-fetch query
        # (return_count_only=False), i.e. what the live service reports
        # post-restore. Defaults to [] (no live features found -- makes
        # verify_restore report every OID as not found, unless a test
        # supplies the live state it wants to check against).
        self._restore_verify_features = (
            list(restore_verify_features) if restore_verify_features else []
        )
        # Simulates verify_restore's own query blowing up (e.g. a real
        # network/API error), to test that rollback.py surfaces this as
        # verify_error rather than letting it look identical to "nothing to
        # verify" (both would otherwise report matched=0 mismatched=0).
        self._raise_on_verify_query = raise_on_verify_query

    def query(self, where=None, out_fields=None, out_sr=None,
              return_all_records=None, return_count_only=False):
        self.query_calls.append({"where": where, "return_count_only": return_count_only})
        if return_count_only:
            if self._query_returns:
                return self._query_returns.pop(0)
            return 0
        if self._raise_on_verify_query:
            raise RuntimeError("simulated verify query failure")
        return SimpleNamespace(
            features=[_FakeFeature(f) for f in self._restore_verify_features]
        )

    def edit_features(self, adds=None, updates=None, deletes=None):
        self.calls.append({"adds": adds, "updates": updates, "deletes": deletes})
        if deletes is not None:
            return {"deleteResults": [{"objectId": oid, "success": True} for oid in deletes]}
        if updates is not None:
            return {
                "updateResults": [
                    {"objectId": u["attributes"].get("OBJECTID"), "success": True}
                    for u in updates
                ]
            }
        return {}


def _write_report(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_rollback_deletes_appended_features_when_layer_matches(tmp_path):
    backup_path = tmp_path / "hydrant_valves_20260101_000000.json"
    report_path = tmp_path / "hydrant_valves_20260101_000000.csv"
    ledger_path = tmp_path / "hydrant_valves.json"

    write_backup([], backup_path, "hydrant_valves", "https://example.com/FeatureServer/9")
    _write_report(
        report_path,
        [
            {"captured_global_id": "g1", "action": "appended", "authoritative_oid": "1", "layer": "hydrant_valves"},
            {"captured_global_id": "g2", "action": "appended", "authoritative_oid": "2", "layer": "hydrant_valves"},
        ],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )

    layer = FakeLayer()
    rollback(str(backup_path), str(report_path), layer, str(ledger_path), expected_layer_name="hydrant_valves")

    assert len(layer.calls) == 1
    assert layer.calls[0]["deletes"] == [1, 2]

    ledger = load_ledger(str(ledger_path))
    assert ledger == {}


def test_rollback_refuses_when_layer_argument_does_not_match_backup(tmp_path):
    """Reproduces the incident: --layer resolves to a different layer than the
    backup/report were generated for. Must raise before any edit_features call."""
    backup_path = tmp_path / "hydrant_valves_20260101_000000.json"
    report_path = tmp_path / "hydrant_valves_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"

    write_backup([], backup_path, "hydrant_valves", "https://example.com/FeatureServer/9")
    _write_report(
        report_path,
        [{"captured_global_id": "g1", "action": "appended", "authoritative_oid": "1", "layer": "hydrant_valves"}],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )

    layer = FakeLayer()
    with pytest.raises(LayerMismatchError):
        rollback(str(backup_path), str(report_path), layer, str(ledger_path), expected_layer_name="hydrants")

    # The whole point of the guard: no write -- and no live read either --
    # should have happened.
    assert layer.calls == []
    assert layer.query_calls == []


def test_rollback_refuses_when_report_layer_does_not_match(tmp_path):
    """Even if the backup file has no entries (all-append run, so nothing to
    mismatch there), a report-recorded layer mismatch alone must still block."""
    backup_path = tmp_path / "hydrant_valves_20260101_000000.json"
    report_path = tmp_path / "hydrant_valves_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"

    # Backup has no layer recorded (simulates a caller that forgot to pass it,
    # or an old file) but the report does, and it disagrees with --layer.
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump([], f)
    _write_report(
        report_path,
        [{"captured_global_id": "g1", "action": "appended", "authoritative_oid": "1", "layer": "hydrant_valves"}],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )

    layer = FakeLayer()
    with pytest.raises(LayerMismatchError):
        rollback(str(backup_path), str(report_path), layer, str(ledger_path), expected_layer_name="hydrants")

    assert layer.calls == []
    assert layer.query_calls == []


def test_rollback_refuses_legacy_files_with_no_recorded_layer_by_default(tmp_path):
    """Backups/reports written before this guard existed have no layer
    metadata at all, so --layer can't be verified against them. This is
    exactly the ambiguity that caused the original incident (a --layer typo
    against a pre-guard backup/report silently deleted the wrong live
    layer), so it must fail closed by default rather than warn-and-proceed."""
    backup_path = tmp_path / "hydrants_20260101_000000.json"
    report_path = tmp_path / "hydrants_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump([], f)
    _write_report(
        report_path,
        [{"captured_global_id": "g1", "action": "appended", "authoritative_oid": "1"}],
        fieldnames=["captured_global_id", "action", "authoritative_oid"],
    )

    layer = FakeLayer()
    with pytest.raises(LayerMismatchError):
        rollback(str(backup_path), str(report_path), layer, str(ledger_path), expected_layer_name="hydrants")

    assert layer.calls == []
    assert layer.query_calls == []


def test_rollback_allows_legacy_files_with_force(tmp_path):
    """The same unverifiable legacy scenario proceeds when force=True is
    passed explicitly (CLI: --force) — a deliberate opt-in, not a default."""
    backup_path = tmp_path / "hydrants_20260101_000000.json"
    report_path = tmp_path / "hydrants_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump([], f)
    _write_report(
        report_path,
        [{"captured_global_id": "g1", "action": "appended", "authoritative_oid": "1"}],
        fieldnames=["captured_global_id", "action", "authoritative_oid"],
    )

    layer = FakeLayer()
    rollback(
        str(backup_path), str(report_path), layer, str(ledger_path),
        expected_layer_name="hydrants", force=True,
    )

    assert len(layer.calls) == 1
    assert layer.calls[0]["deletes"] == [1]


def test_rollback_restore_excludes_system_fields(tmp_path):
    """Restoring an "updated" row must not send system/editor-tracking fields
    (GlobalID, Creator, CreationDate, Editor, EditDate) back through
    edit_features -- the backup snapshot captures the full raw feature
    (including those AGOL-managed fields), but the restore payload must be
    filtered the same way build_field_updates filters normal apply payloads.
    """
    backup_path = tmp_path / "hydrants_20260101_000000.json"
    report_path = tmp_path / "hydrants_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"

    write_backup(
        [
            {
                "oid": 5,
                "attributes": {
                    "OBJECTID": 5,
                    "GlobalID": "{aaaa}",
                    "Creator": "someone",
                    "CreationDate": 1700000000000,
                    "Editor": "someone",
                    "EditDate": 1700000000000,
                    "Notes": "original notes",
                },
                "geometry": {"x": 1.0, "y": 2.0},
            }
        ],
        backup_path,
        "hydrants",
        "https://example.com/FeatureServer/8",
    )
    _write_report(
        report_path,
        [{"captured_global_id": "g1", "action": "updated", "authoritative_oid": "5", "layer": "hydrants"}],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )

    # Matches the backup so post-restore verification doesn't report a
    # (harmless-here-but-noisy) mismatch unrelated to what this test checks.
    layer = FakeLayer(restore_verify_features=[
        {
            "attributes": {"OBJECTID": 5, "GlobalID": "{aaaa}", "Notes": "original notes"},
            "geometry": {"x": 1.0, "y": 2.0},
        }
    ])
    rollback(str(backup_path), str(report_path), layer, str(ledger_path), expected_layer_name="hydrants")

    assert len(layer.calls) == 1
    sent_attrs = layer.calls[0]["updates"][0]["attributes"]

    for excluded in ("GlobalID", "Creator", "CreationDate", "Editor", "EditDate"):
        assert excluded not in sent_attrs, f"{excluded} must not be sent in a restore payload"

    # The identifying OID and the actual restored data must still be present.
    assert sent_attrs["OBJECTID"] == 5
    assert sent_attrs["Notes"] == "original notes"


def test_rollback_without_expected_layer_name_skips_the_check(tmp_path):
    """Backward compatibility: callers that don't pass expected_layer_name
    (the parameter is keyword-only and optional) get the old, unchecked
    behavior."""
    backup_path = tmp_path / "hydrants_20260101_000000.json"
    report_path = tmp_path / "hydrants_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"

    write_backup([], backup_path, "hydrants", "https://example.com/FeatureServer/8")
    _write_report(
        report_path,
        [{"captured_global_id": "g1", "action": "appended", "authoritative_oid": "1", "layer": "hydrants"}],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )

    layer = FakeLayer()
    rollback(str(backup_path), str(report_path), layer, str(ledger_path))

    assert len(layer.calls) == 1


def test_rollback_warns_on_feature_count_mismatch(tmp_path, caplog):
    """If the live count after rollback doesn't match count_before minus the
    number of successful feature deletes, this should be surfaced as a
    warning -- but must never raise or change what rollback actually did."""
    backup_path = tmp_path / "hydrants_20260101_000000.json"
    report_path = tmp_path / "hydrants_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"

    write_backup([], backup_path, "hydrants", "https://example.com/FeatureServer/8")
    _write_report(
        report_path,
        [
            {"captured_global_id": "g1", "action": "appended", "authoritative_oid": "1", "layer": "hydrants"},
            {"captured_global_id": "g2", "action": "appended", "authoritative_oid": "2", "layer": "hydrants"},
        ],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )

    # before=10, after=10 -- but 2 features were (fake-)deleted, so the
    # expected after-count is 8. Mismatch should be logged.
    layer = FakeLayer(query_returns=[10, 10])
    with caplog.at_level("WARNING"):
        rollback(str(backup_path), str(report_path), layer, str(ledger_path), expected_layer_name="hydrants")

    assert layer.query_calls == [
        {"where": "1=1", "return_count_only": True},
        {"where": "1=1", "return_count_only": True},
    ]
    assert any("does not match" in record.message for record in caplog.records)


def test_rollback_silent_on_feature_count_match(tmp_path, caplog):
    """No warning should fire when the live after-count matches the expected
    within-run delta."""
    backup_path = tmp_path / "hydrants_20260101_000000.json"
    report_path = tmp_path / "hydrants_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"

    write_backup([], backup_path, "hydrants", "https://example.com/FeatureServer/8")
    _write_report(
        report_path,
        [
            {"captured_global_id": "g1", "action": "appended", "authoritative_oid": "1", "layer": "hydrants"},
            {"captured_global_id": "g2", "action": "appended", "authoritative_oid": "2", "layer": "hydrants"},
        ],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )

    # before=10, after=8 -- matches 10 - 2 successful deletes exactly.
    layer = FakeLayer(query_returns=[10, 8])
    with caplog.at_level("WARNING"):
        rollback(str(backup_path), str(report_path), layer, str(ledger_path), expected_layer_name="hydrants")

    assert not any("does not match" in record.message for record in caplog.records)


def test_rollback_writes_log_when_log_path_given(tmp_path):
    """When log_path is passed, a JSON audit log of the rollback run is
    written with the before/after counts and per-row outcomes."""
    backup_path = tmp_path / "hydrants_20260101_000000.json"
    report_path = tmp_path / "hydrants_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"
    log_path = tmp_path / "hydrants_20260101_000000_rollback_20260101_010000.json"

    write_backup([], backup_path, "hydrants", "https://example.com/FeatureServer/8")
    _write_report(
        report_path,
        [{"captured_global_id": "g1", "action": "appended", "authoritative_oid": "1", "layer": "hydrants"}],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )
    # g1 must already be in the ledger (as the prior apply run would have
    # left it) for rollback to have anything to clear.
    ledger_path.write_text(
        json.dumps({"g1": {"action": "created", "authoritative_oid": 1, "attachments_status": "0/0", "run_time": "x"}}),
        encoding="utf-8",
    )

    layer = FakeLayer(query_returns=[5, 4])
    rollback(
        str(backup_path), str(report_path), layer, str(ledger_path),
        expected_layer_name="hydrants", log_path=str(log_path),
    )

    assert log_path.exists()
    log_data = json.loads(log_path.read_text(encoding="utf-8"))
    assert log_data["layer"] == "hydrants"
    assert log_data["authoritative_feature_count_before"] == 5
    assert log_data["authoritative_feature_count_after"] == 4
    assert log_data["expected_feature_count_after"] == 4
    assert log_data["feature_count_matches_expected"] is True
    assert log_data["delete_feature_success_count"] == 1
    assert log_data["feature_delete_results"] == [{"oid": 1, "success": True, "error": None}]
    assert log_data["ledger_cleared_count"] == 1


def test_rollback_log_records_post_restore_verification(tmp_path):
    """rollback() must run verify_restore against the live layer after
    restoring, and record its per-OID results (not just apply_updates'
    accepted/rejected outcome) in the audit log."""
    backup_path = tmp_path / "hydrants_20260101_000000.json"
    report_path = tmp_path / "hydrants_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"
    log_path = tmp_path / "hydrants_20260101_000000_rollback_20260101_010000.json"

    write_backup(
        [
            {
                "oid": 5,
                "attributes": {"OBJECTID": 5, "GlobalID": "{aaaa}", "Notes": "original notes"},
                "geometry": {"x": 1.0, "y": 2.0},
            }
        ],
        backup_path,
        "hydrants",
        "https://example.com/FeatureServer/8",
    )
    _write_report(
        report_path,
        [{"captured_global_id": "g1", "action": "updated", "authoritative_oid": "5", "layer": "hydrants"}],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )

    # Live service reports OID 5 back with the exact restored state --
    # verify_restore should find this a clean match.
    layer = FakeLayer(
        restore_verify_features=[
            {
                "attributes": {"OBJECTID": 5, "GlobalID": "{aaaa}", "Notes": "original notes"},
                "geometry": {"x": 1.0, "y": 2.0},
            }
        ],
    )
    rollback(
        str(backup_path), str(report_path), layer, str(ledger_path),
        expected_layer_name="hydrants", log_path=str(log_path),
    )

    log_data = json.loads(log_path.read_text(encoding="utf-8"))
    assert log_data["verify_success_count"] == 1
    assert log_data["verify_mismatch_count"] == 0
    assert log_data["verify_results"] == [
        {
            "oid": 5,
            "live_feature_found": True,
            "verified": True,
            "attribute_mismatches": {},
            "geometry_mismatch_m": 0.0,
        }
    ]


def test_rollback_log_records_post_restore_verification_mismatch(tmp_path, caplog):
    """A live feature that doesn't match the backup after restoring must be
    reported as a mismatch, not silently counted alongside genuine
    successes -- and logged so it's visible without opening the JSON."""
    backup_path = tmp_path / "hydrants_20260101_000000.json"
    report_path = tmp_path / "hydrants_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"
    log_path = tmp_path / "hydrants_20260101_000000_rollback_20260101_010000.json"

    write_backup(
        [
            {
                "oid": 5,
                "attributes": {"OBJECTID": 5, "GlobalID": "{aaaa}", "Notes": "original notes"},
                "geometry": {"x": 1.0, "y": 2.0},
            }
        ],
        backup_path,
        "hydrants",
        "https://example.com/FeatureServer/8",
    )
    _write_report(
        report_path,
        [{"captured_global_id": "g1", "action": "updated", "authoritative_oid": "5", "layer": "hydrants"}],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )

    # Live service reports a different Notes value than the backup expected.
    layer = FakeLayer(
        restore_verify_features=[
            {
                "attributes": {"OBJECTID": 5, "GlobalID": "{aaaa}", "Notes": "STILL WRONG"},
                "geometry": {"x": 1.0, "y": 2.0},
            }
        ],
    )
    with caplog.at_level("WARNING"):
        rollback(
            str(backup_path), str(report_path), layer, str(ledger_path),
            expected_layer_name="hydrants", log_path=str(log_path),
        )

    log_data = json.loads(log_path.read_text(encoding="utf-8"))
    assert log_data["verify_success_count"] == 0
    assert log_data["verify_mismatch_count"] == 1
    assert log_data["verify_results"][0]["verified"] is False
    assert log_data["verify_results"][0]["attribute_mismatches"] == {
        "Notes": ["original notes", "STILL WRONG"]
    }
    assert any("verification mismatch" in record.message for record in caplog.records)


def test_rollback_log_distinguishes_verify_crash_from_nothing_to_verify(tmp_path, caplog):
    """A verify_restore crash must be visibly different in the log from a
    rollback with no restores to check -- both would otherwise report
    verify_success_count=0/verify_mismatch_count=0, hiding a real failure
    behind what looks like "nothing to verify"."""
    backup_path = tmp_path / "hydrants_20260101_000000.json"
    report_path = tmp_path / "hydrants_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"
    log_path = tmp_path / "hydrants_20260101_000000_rollback_20260101_010000.json"

    write_backup(
        [{"oid": 5, "attributes": {"OBJECTID": 5, "Notes": "x"}, "geometry": {"x": 1.0, "y": 2.0}}],
        backup_path, "hydrants", "https://example.com/FeatureServer/8",
    )
    _write_report(
        report_path,
        [{"captured_global_id": "g1", "action": "updated", "authoritative_oid": "5", "layer": "hydrants"}],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )

    layer = FakeLayer(raise_on_verify_query=True)
    with caplog.at_level("ERROR"):
        rollback(
            str(backup_path), str(report_path), layer, str(ledger_path),
            expected_layer_name="hydrants", log_path=str(log_path),
        )

    log_data = json.loads(log_path.read_text(encoding="utf-8"))
    assert log_data["verify_success_count"] is None
    assert log_data["verify_mismatch_count"] is None
    assert log_data["verify_error"] is not None
    assert "simulated verify query failure" in log_data["verify_error"]
    assert any("Post-restore verification failed to run" in r.message for r in caplog.records)


def test_rollback_log_write_failure_does_not_raise(tmp_path):
    """A failure writing the audit log (e.g. bad path) must not surface as
    an exception from rollback() -- the rollback itself already succeeded
    and its ledger state was already saved by that point."""
    backup_path = tmp_path / "hydrants_20260101_000000.json"
    report_path = tmp_path / "hydrants_20260101_000000.csv"
    ledger_path = tmp_path / "hydrants.json"
    # A directory can't be opened for writing as a file -- forces write_rollback_log to raise.
    bad_log_path = tmp_path

    write_backup([], backup_path, "hydrants", "https://example.com/FeatureServer/8")
    _write_report(
        report_path,
        [{"captured_global_id": "g1", "action": "appended", "authoritative_oid": "1", "layer": "hydrants"}],
        fieldnames=["captured_global_id", "action", "authoritative_oid", "layer"],
    )

    layer = FakeLayer()
    rollback(
        str(backup_path), str(report_path), layer, str(ledger_path),
        expected_layer_name="hydrants", log_path=str(bad_log_path),
    )

    ledger = load_ledger(str(ledger_path))
    assert ledger == {}
