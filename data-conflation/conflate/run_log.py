"""Durable activity log for a rollback run.

Records what a rollback run actually did against AGOL -- per-row restore,
feature-delete, and attachment-delete outcomes, before/after authoritative
feature counts, and post-restore verify_restore diffs against the backup
snapshot -- as a JSON artifact, since rollback.py itself only logs a
one-line console summary.
"""

import json
import os


def write_rollback_log(
    path,
    *,
    layer: str,
    authoritative_url: str,
    backup_path: str,
    report_path: str,
    authoritative_feature_count_before: int,
    authoritative_feature_count_after: int,
    expected_feature_count_after: int,
    restore_results: list[dict],
    feature_delete_results: list[dict],
    attachment_delete_results: list[dict],
    total_attachments_targeted: int,
    total_attachments_removed: int,
    restore_success_count: int,
    restore_failure_count: int,
    delete_feature_success_count: int,
    delete_feature_failure_count: int,
    ledger_cleared_count: int,
    verify_results: list[dict],
    verify_success_count: int | None,
    verify_mismatch_count: int | None,
    verify_error: str | None,
) -> None:
    """Write a JSON record of a rollback run's inputs, outcomes,
    before/after authoritative feature counts, and post-restore
    verify_restore results to ``path``.

    Creates the parent directory of ``path`` if it doesn't exist and isn't
    an empty string. This is a write-only audit artifact -- nothing in the
    tool reads it back.
    """
    parent = os.path.dirname(str(path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    payload = {
        "layer": layer,
        "authoritative_url": authoritative_url,
        "backup_path": backup_path,
        "report_path": report_path,
        "authoritative_feature_count_before": authoritative_feature_count_before,
        "authoritative_feature_count_after": authoritative_feature_count_after,
        "expected_feature_count_after": expected_feature_count_after,
        "feature_count_matches_expected": (
            authoritative_feature_count_after == expected_feature_count_after
        ),
        "restore_results": restore_results,
        "feature_delete_results": feature_delete_results,
        "attachment_delete_results": attachment_delete_results,
        "total_attachments_targeted": total_attachments_targeted,
        "total_attachments_removed": total_attachments_removed,
        "restore_success_count": restore_success_count,
        "restore_failure_count": restore_failure_count,
        "delete_feature_success_count": delete_feature_success_count,
        "delete_feature_failure_count": delete_feature_failure_count,
        "ledger_cleared_count": ledger_cleared_count,
        "verify_results": verify_results,
        "verify_success_count": verify_success_count,
        "verify_mismatch_count": verify_mismatch_count,
        "verify_error": verify_error,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
