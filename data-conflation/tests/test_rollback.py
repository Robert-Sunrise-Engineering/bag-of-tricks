"""Tests for conflate.rollback, in particular the --layer mismatch guard.

This guard exists because of a real incident: running --rollback with a
--layer that didn't match the layer a backup/report were generated for
caused deletes to hit an unrelated, already-populated authoritative layer.
"""

import csv
import json

import pytest

from conflate.backup import write_backup
from conflate.ledger import load_ledger
from conflate.rollback import rollback, LayerMismatchError


class FakeLayer:
    """Records edit_features calls; fails the test if called when it shouldn't be."""

    def __init__(self):
        self.calls = []

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

    # The whole point of the guard: no write should have happened.
    assert layer.calls == []


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
