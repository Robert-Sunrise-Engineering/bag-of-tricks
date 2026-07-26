"""Roll back a prior conflation run using its backup snapshot and report.

Given the report CSV produced for a run and the pre-write backup JSON
captured before that run's writes, this module restores updated
authoritative features to their pre-edit state and deletes features that
were appended by the run.
"""

import csv
import logging

from conflate.apply import apply_updates
from conflate.backup import load_backup

logger = logging.getLogger(__name__)


def rollback(backup_path, report_path, layer) -> None:
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

    Behavior:
        - Rows with action == "updated" are restored to their pre-edit
          attributes/geometry (from the backup) via apply_updates.
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

    # --- Apply restores ---
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

    # --- Apply deletes ---
    delete_success_count = 0
    delete_failure_count = 0
    if oids_to_delete:
        delete_result = layer.edit_features(deletes=oids_to_delete)
        delete_results = delete_result.get("deleteResults", [])
        for i, dr in enumerate(delete_results):
            oid = dr.get("objectId")
            if oid is None and i < len(oids_to_delete):
                oid = oids_to_delete[i]
            if dr.get("success"):
                delete_success_count += 1
                logger.info("Deleted appended OID %s", oid)
            else:
                delete_failure_count += 1
                logger.error(
                    "Failed to delete appended OID %s: %s",
                    oid,
                    dr.get("error"),
                )

    # --- Final summary ---
    logger.info(
        "Rollback complete: restores succeeded=%d failed=%d; "
        "deletes succeeded=%d failed=%d",
        restore_success_count,
        restore_failure_count,
        delete_success_count,
        delete_failure_count,
    )
