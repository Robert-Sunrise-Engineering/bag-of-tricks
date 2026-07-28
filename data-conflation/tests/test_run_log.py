"""Tests for conflate.run_log.write_rollback_log."""

import json

from conflate.run_log import write_rollback_log


def test_write_rollback_log_round_trip(tmp_path):
    log_path = tmp_path / "nested" / "hydrants_20260101_000000_rollback_20260101_010000.json"

    write_rollback_log(
        log_path,
        layer="hydrants",
        authoritative_url="https://example.com/FeatureServer/8",
        backup_path="backups/hydrants_20260101_000000.json",
        report_path="reports/hydrants_20260101_000000.csv",
        authoritative_feature_count_before=10,
        authoritative_feature_count_after=8,
        expected_feature_count_after=8,
        restore_results=[{"oid": 5, "success": True, "error": None}],
        feature_delete_results=[{"oid": 1, "success": True, "error": None}],
        attachment_delete_results=[{"oid": 5, "attachment_id": 99, "success": True, "error": None}],
        total_attachments_targeted=1,
        total_attachments_removed=1,
        restore_success_count=1,
        restore_failure_count=0,
        delete_feature_success_count=2,
        delete_feature_failure_count=0,
        ledger_cleared_count=3,
        verify_results=[{"oid": 5, "live_feature_found": True, "verified": True, "attribute_mismatches": {}, "geometry_mismatch_m": 0.0}],
        verify_success_count=1,
        verify_mismatch_count=0,
        verify_error=None,
    )

    # Creates the parent directory (mirrors write_backup) even though it
    # didn't exist yet.
    assert log_path.exists()

    data = json.loads(log_path.read_text(encoding="utf-8"))
    assert data["layer"] == "hydrants"
    assert data["authoritative_feature_count_before"] == 10
    assert data["authoritative_feature_count_after"] == 8
    assert data["feature_count_matches_expected"] is True
    assert data["restore_results"] == [{"oid": 5, "success": True, "error": None}]
    assert data["total_attachments_removed"] == 1
    assert data["ledger_cleared_count"] == 3
    assert data["verify_success_count"] == 1
    assert data["verify_mismatch_count"] == 0
    assert data["verify_error"] is None
    assert data["verify_results"][0]["oid"] == 5
