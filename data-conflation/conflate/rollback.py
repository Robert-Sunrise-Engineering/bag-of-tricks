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
from conflate.backup import load_backup_meta
from conflate.ledger import load_ledger, save_ledger
from conflate.attachments import delete_attachments_batch

logger = logging.getLogger(__name__)


class LayerMismatchError(Exception):
    """Raised when --layer doesn't match the layer a backup/report was generated for."""


def _check_layer_match(
    backup_meta: dict, report_rows: list[dict], expected_layer_name: str, force: bool = False
) -> None:
    """Raise LayerMismatchError if the backup or report were recorded for a
    different layer than ``expected_layer_name`` (the CLI's --layer value).

    Legacy backups/reports with no recorded layer name (written before this
    guard existed) can't be checked at all — that is exactly the situation
    that let a --layer typo silently delete the wrong live layer in the
    first place, so this fails closed by default and requires ``force=True``
    (an explicit, deliberate opt-in) to proceed anyway.
    """
    backup_layer = backup_meta.get("layer")
    if backup_layer is not None and backup_layer != expected_layer_name:
        raise LayerMismatchError(
            f"Refusing to roll back: --layer was {expected_layer_name!r}, but the "
            f"backup file was recorded for layer {backup_layer!r}. Pass "
            f"--layer {backup_layer!r} instead, or double-check you have the "
            f"right backup file."
        )

    report_layers = {row.get("layer") for row in report_rows if row.get("layer")}
    if report_layers and report_layers != {expected_layer_name}:
        raise LayerMismatchError(
            f"Refusing to roll back: --layer was {expected_layer_name!r}, but the "
            f"report file was recorded for layer(s) {sorted(report_layers)!r}. Pass "
            f"the matching --layer instead, or double-check you have the right "
            f"report file."
        )

    if backup_layer is None and not report_layers:
        if not force:
            raise LayerMismatchError(
                f"Refusing to roll back: neither the backup nor the report file has "
                f"a recorded layer identity (they predate this safety check), so "
                f"--layer {expected_layer_name!r} cannot be verified against them. "
                f"Manually confirm these files were actually produced for layer "
                f"{expected_layer_name!r}, then pass force=True (CLI: --force) to "
                f"proceed anyway."
            )
        logger.warning(
            "Backup and report have no recorded layer identity (written before "
            "this check existed); proceeding without verifying --layer %r is "
            "correct for these files, because force=True was passed.",
            expected_layer_name,
        )


def rollback(
    backup_path,
    report_path,
    layer,
    ledger_path,
    *,
    expected_layer_name=None,
    force=False,
) -> None:
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
        expected_layer_name: The config.json layer key the caller resolved
                     ``layer`` from (i.e. the CLI's ``--layer`` value). If
                     given, and the backup and/or report recorded a
                     different layer name, this raises ``LayerMismatchError``
                     *before* touching ``layer`` at all. Backups/reports
                     written before layer identity was tracked have no
                     recorded name at all and can't be checked either way;
                     that ambiguity is exactly what caused a --layer typo to
                     silently delete the wrong live layer previously, so
                     those also raise LayerMismatchError unless ``force``
                     is set.
        force: When True, allows the rollback to proceed against a
                     backup/report with no recorded layer identity (see
                     ``expected_layer_name`` above). Has no effect on an
                     actual recorded mismatch — that always raises. Ignored
                     if ``expected_layer_name`` is None.

    Behavior:
        - Rows with action == "updated" are restored to their pre-edit
          attributes/geometry (from the backup) via apply_updates.
          Any attachments added during the run are also removed.
        - Rows with action == "appended" are deleted via
          layer.edit_features(deletes=...).

    Returns:
        None. Propagates FileNotFoundError if backup_path or report_path
        don't exist. Raises LayerMismatchError if expected_layer_name is
        given and doesn't match what the backup/report recorded (or can't
        be verified and force wasn't set).
    """
    with open(report_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        report_rows = list(reader)

    backup_meta = load_backup_meta(backup_path)
    backup_entries = backup_meta["entries"]
    backup_lookup = {entry["oid"]: entry for entry in backup_entries}

    if expected_layer_name is not None:
        _check_layer_match(backup_meta, report_rows, expected_layer_name, force=force)

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
