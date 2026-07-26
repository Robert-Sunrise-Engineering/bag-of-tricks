"""Roll back a prior conflation run using its backup snapshot and report.

Given the report CSV produced for a run and the pre-write backup JSON
captured before that run's writes, this module restores updated
authoritative features to their pre-edit state and deletes features that
were appended by the run. Also removes attachments added during the run.
"""

import csv
import json
import logging

from conflate.apply import apply_updates
from conflate.backup import load_backup
from conflate.ledger import load_ledger, save_ledger
from conflate.attachments import delete_attachments_batch

logger = logging.getLogger(__name__)


def rollback(backup_path, report_path, layer, ledger_path) -> None:
    """
    Undo a prior run's writes to ``layer`` using its backup and report.

    Args:
        backup_path: Path to the JSON backup file (see ``backup.write_backup``)
                     containing the pre-edit state of every feature the run
                     updated.
        report_path: Path to the CSV report produced for the run. Must have
                     at least the columns "captured_global_id", "action",
                     and "authoritative_oid". Rows whose "action" is not
                     exactly "updated" or "appended" are ignored.
        layer: An AGOL FeatureLayer object with an edit_features method.
        ledger_path: Path to the ledger JSON file for the layer being rolled
                     back (see ``ledger.load_ledger``/``save_ledger``). Entries
                     for captured features processed by this run are cleared
                     so a subsequent run will reconsider them.

    Behavior:
        - Rows with action == "updated" are restored to their pre-edit
          attributes/geometry (from the backup) via apply_updates.
          Any attachments added during the run are also removed.
        - Rows with action == "appended" are deleted via
          layer.edit_features(deletes=...).

    Returns:
        None. Propagates FileNotFoundError if backup_path or report_path
        don't exist.
    """
    with open(report_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        report_rows = list(reader)

    backup_entries = load_backup(backup_path)
    backup_lookup = {entry["oid"]: entry for entry in backup_entries}

    # --- Step 1: build restores for updated rows ---
    restores = []
    for row in report_rows:
        action = row.get("action")
        if action != "updated":
            continue

        oid_raw = row.get("authoritative_oid")
        try:
            oid = int(oid_raw)
        except (TypeError, ValueError):
            logger.warning(
                "Skipping updated row with invalid authoritative_oid %r "
                "(captured_global_id=%s)",
                oid_raw,
                row.get("captured_global_id"),
            )
            continue

        backup_entry = backup_lookup.get(oid)
        if backup_entry is None:
            logger.warning(
                "No backup entry found for authoritative_oid %s; skipping restore",
                oid,
            )
            continue

        restore = {
            "attributes": {**backup_entry["attributes"], "OBJECTID": oid},
            "geometry": backup_entry.get("geometry"),
        }
        restores.append(restore)

    # --- Step 2: collect OIDs to delete for appended rows ---
    oids_to_delete = []
    for row in report_rows:
        action = row.get("action")
        if action != "appended":
            continue

        oid_raw = row.get("authoritative_oid")
        try:
            oid = int(oid_raw)
        except (TypeError, ValueError):
            logger.warning(
                "Skipping appended row with invalid authoritative_oid %r "
                "(captured_global_id=%s)",
                oid_raw,
                row.get("captured_global_id"),
            )
            continue

        oids_to_delete.append(oid)

    # --- Step 3: collect attachments to delete for updated rows ---
    attachments_to_remove = {}  # authoritative_oid -> list of attachment_ids
    for row in report_rows:
        action = row.get("action")
        if action != "updated":
            continue

        oid_raw = row.get("authoritative_oid")
        try:
            oid = int(oid_raw)
        except (TypeError, ValueError):
            continue

        # attachments_added is stored in the report as a JSON-encoded list
        # (see cli.py), since csv.DictWriter would otherwise stringify a
        # raw Python list into something isinstance(..., list) can't detect.
        try:
            attachment_ids_added = json.loads(row.get("attachments_added") or "[]")
        except (json.JSONDecodeError, TypeError):
            attachment_ids_added = []

        if attachment_ids_added:
            if oid not in attachments_to_remove:
                attachments_to_remove[oid] = []
            attachments_to_remove[oid].extend(attachment_ids_added)

    # --- Step 4: Apply restores ---
    restore_success_count = 0
    restore_failure_count = 0
    if restores:
        results = apply_updates(layer, restores)
        for result in results:
            input_ref = result.get("input_ref") or {}
            oid = input_ref.get("attributes", {}).get("OBJECTID")
            if result.get("success"):
                restore_success_count += 1
                logger.info("Restored OID %s to pre-edit state", oid)
            else:
                restore_failure_count += 1
                logger.error(
                    "Failed to restore OID %s: %s", oid, result.get("error")
                )

    # --- Step 5: Delete attachments added during the run ---
    delete_attachments_success = 0
    delete_attachments_failed = 0
    if attachments_to_remove:
        attachment_results = delete_attachments_batch(layer, attachments_to_remove)
        for ar in attachment_results:
            oid = ar.get("authoritative_oid")
            if ar.get("success"):
                # Count successful deletions
                error_count = len(ar.get("errors", []))
                total_to_delete = len(attachments_to_remove.get(oid, []))
                if error_count == 0:
                    delete_attachments_success += 1
                    logger.info("Deleted %d attachments from OID %s", total_to_delete, oid)
                else:
                    delete_attachments_failed += 1
                    logger.warning(
                        "Partially failed to delete attachments from OID %s: %d errors",
                        oid, error_count
                    )
            else:
                delete_attachments_failed += 1
                logger.error(
                    "Failed to delete attachments from OID %s: %s",
                    oid,
                    "; ".join(ar.get("errors", [])),
                )

    # --- Step 6: Delete appended rows ---
    delete_feature_success_count = 0
    delete_feature_failure_count = 0
    if oids_to_delete:
        delete_result = layer.edit_features(deletes=oids_to_delete)
        delete_results = delete_result.get("deleteResults", [])
        for i, dr in enumerate(delete_results):
            oid = dr.get("objectId")
            if oid is None and i < len(oids_to_delete):
                oid = oids_to_delete[i]
            if dr.get("success"):
                delete_feature_success_count += 1
                logger.info("Deleted appended OID %s", oid)
            else:
                delete_feature_failure_count += 1
                logger.error(
                    "Failed to delete appended OID %s: %s",
                    oid,
                    dr.get("error"),
                )

    # --- Step 7: reset ledger entries for processed captured features ---
    ledger = load_ledger(ledger_path)
    cleared_count = 0
    for row in report_rows:
        action = row.get("action")
        if action not in ("updated", "appended"):
            continue

        captured_global_id = row.get("captured_global_id")
        if captured_global_id is None:
            logger.warning(
                "Skipping row with missing captured_global_id (action=%s)",
                action,
            )
            continue

        if ledger.pop(captured_global_id, None) is not None:
            cleared_count += 1
    save_ledger(ledger_path, ledger)

    # --- Final summary ---
    logger.info(
        "Rollback complete: restores succeeded=%d failed=%d; "
        "attachment cleanup succeeded=%d failed=%d; "
        "feature deletes succeeded=%d failed=%d; "
        "ledger entries cleared for %d captured features",
        restore_success_count,
        restore_failure_count,
        delete_attachments_success,
        delete_attachments_failed,
        delete_feature_success_count,
        delete_feature_failure_count,
        cleared_count,
    )
