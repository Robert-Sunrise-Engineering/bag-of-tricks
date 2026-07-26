"""Command-line orchestration entry point for the AGOL conflation tool.

Wires together the pure-logic and AGOL-integration modules in ``conflate/``
into a single runnable workflow:

  * dry run (default): compute matches, write a "would_*" report, no writes.
  * ``--apply``: back up affected authoritative features, apply updates/appends,
    copy attachments, ledger successfully-completed features, write a report
    of actual outcomes.
  * ``--rollback <backup-file>``: delegate to ``conflate.rollback.rollback``.

This module intentionally contains no matching/threshold/edit logic itself —
it only sequences calls into the other ``conflate`` modules.
"""

import argparse
import logging
import os
from datetime import datetime

from conflate.config import load_config, load_local_config, validate_layer_config
from conflate.gis_client import connect, get_layer, validate_schema, validate_capabilities
from conflate.paging import fetch_all_features
from conflate.threshold import format_threshold_both_units
from conflate.matching import find_candidates, pick_closest
from conflate.nullfill import build_field_updates
from conflate.ledger import load_ledger, save_ledger, mark_processed, is_processed
from conflate.report import write_report
from conflate.backup import write_backup
from conflate.attachments import copy_attachments
from conflate.apply import apply_updates, apply_appends

logger = logging.getLogger(__name__)

# Fields that must never be written by null-fill or append payloads.
EXCLUDED_FIELDS = {
    "OBJECTID",
    "GlobalID",
    "Shape",
    "SHAPE",
    "Creator",
    "CreationDate",
    "Editor",
    "EditDate",
}


def _simplify_feature(raw_feature: dict) -> dict:
    """Flatten an AGOL feature dict ({"attributes": {...}, "geometry": {...}})
    into a simplified dict with "lon"/"lat" keys plus all attributes flattened in.

    geometry.x/.y are WGS84 lon/lat: fetch_all_features queries with
    out_sr=4326, so every feature reaching this function is already
    reprojected regardless of the source layer's native spatial reference.
    """
    attrs = dict(raw_feature.get("attributes", {}))
    geom = raw_feature.get("geometry") or {}
    simplified = dict(attrs)
    simplified["lon"] = geom.get("x")
    simplified["lat"] = geom.get("y")
    return simplified


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conflate",
        description="Conflate a captured AGOL feature layer into an authoritative one.",
    )
    parser.add_argument(
        "--layer",
        required=True,
        help="Layer name/key in config.json (under the 'layers' object).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually apply updates/appends. Without this flag, this is a dry run.",
    )
    parser.add_argument(
        "--rollback",
        default=None,
        metavar="BACKUP_FILE",
        help="Path to a backup file written by a prior --apply run. If given, "
        "rolls back that run instead of doing a normal conflation run.",
    )
    parser.add_argument(
        "--backup-dir",
        default="backups",
        help="Directory to write/read backup files (default: backups).",
    )
    parser.add_argument(
        "--report-dir",
        default="reports",
        help="Directory to write report CSVs (default: reports).",
    )
    return parser


def _report_path_for_backup(backup_path: str, report_dir: str) -> str:
    """Derive the paired report path from a backup path.

    Backup and report files for the same run share a layer name + timestamp,
    e.g. backups/hydrants_20260725_100000.json <-> reports/hydrants_20260725_100000.csv
    """
    base = os.path.basename(backup_path)
    stem, _ext = os.path.splitext(base)
    return os.path.join(report_dir, f"{stem}.csv")


def _do_rollback(args) -> None:
    from conflate.rollback import rollback

    backup_path = args.rollback
    report_path = _report_path_for_backup(backup_path, args.report_dir)

    config = load_config("config.json")
    layer_cfg = config["layers"][args.layer]
    validate_layer_config(layer_cfg)

    local_config = load_local_config("config.local.json")

    gis = connect(local_config)
    authoritative_layer = get_layer(gis, layer_cfg["authoritative_url"])

    logger.info(
        "Rolling back layer '%s' using backup=%s report=%s",
        args.layer,
        backup_path,
        report_path,
    )
    rollback(backup_path, report_path, authoritative_layer)
    logger.info("Rollback complete.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.rollback:
        _do_rollback(args)
        return

    layer_name = args.layer
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # --- Step 1: load and validate layer config ---
    config = load_config("config.json")
    layer_cfg = config["layers"][layer_name]
    validate_layer_config(layer_cfg)

    # --- Step 2: load local (credentials) config ---
    local_config = load_local_config("config.local.json")

    # --- Step 3: connect, get layers, validate schema/capabilities ---
    gis = connect(local_config)
    authoritative_layer = get_layer(gis, layer_cfg["authoritative_url"])
    captured_layer = get_layer(gis, layer_cfg["captured_url"])

    type_field_authoritative = layer_cfg.get("type_field_authoritative")
    type_field_captured = layer_cfg.get("type_field_captured")

    captured_required_fields = ["GlobalID"]
    authoritative_required_fields = ["GlobalID"]
    if type_field_captured is not None:
        captured_required_fields.append(type_field_captured)
    if type_field_authoritative is not None:
        authoritative_required_fields.append(type_field_authoritative)

    validate_schema(captured_layer, captured_required_fields)
    validate_schema(authoritative_layer, authoritative_required_fields)
    validate_capabilities(authoritative_layer, layer_cfg["copy_attachments"])

    # --- Step 4: bulk-fetch both layers once ---
    logger.info("Fetching all captured features...")
    captured_features_raw = fetch_all_features(captured_layer)
    logger.info("Fetched %d captured features.", len(captured_features_raw))

    logger.info("Fetching all authoritative features...")
    authoritative_features_raw = fetch_all_features(authoritative_layer)
    logger.info("Fetched %d authoritative features.", len(authoritative_features_raw))

    # --- Step 5: match threshold (directly configured, in meters) ---
    threshold_m = layer_cfg["match_threshold_m"]
    logger.info(
        "Match threshold: %s",
        format_threshold_both_units(threshold_m, "meters"),
    )

    # --- Step 6: load ledger ---
    ledger_path = os.path.join("state", f"{layer_name}.json")
    ledger = load_ledger(ledger_path)

    # Simplify authoritative features once; reused for every captured feature.
    authoritative_simplified = [_simplify_feature(f) for f in authoritative_features_raw]
    # Map OBJECTID -> raw (unsimplified) authoritative feature, for real attribute
    # access (build_field_updates, backups) without the injected lon/lat keys.
    authoritative_by_oid = {
        f.get("attributes", {}).get("OBJECTID"): f for f in authoritative_features_raw
    }

    # --- Step 7: plan actions for each unprocessed captured feature ---
    planned_updates = []  # each: {"attributes":..., "geometry":..., "_captured_global_id":..., "_captured_oid":..., "_authoritative_oid":..., "_distance":...}
    planned_appends = []  # each: {"attributes":..., "geometry":..., "_captured_global_id":..., "_captured_oid":...}
    planned_rows = []  # for the report

    for raw_captured in captured_features_raw:
        captured_attrs = raw_captured.get("attributes", {})
        captured_geometry = raw_captured.get("geometry") or {}
        captured_global_id = captured_attrs.get("GlobalID")
        captured_oid = captured_attrs.get("OBJECTID")

        if is_processed(ledger, captured_global_id):
            continue

        # 7a: simplified captured feature dict (lon/lat + flattened attrs)
        captured_simplified = _simplify_feature(raw_captured)
        captured_lon = captured_simplified["lon"]
        captured_lat = captured_simplified["lat"]

        # 7b: find candidates in the bulk-fetched, already-simplified authoritative list
        candidates = find_candidates(
            authoritative_simplified,
            captured_simplified,
            type_field_authoritative,
            type_field_captured,
            threshold_m,
        )

        # 7c: pick closest
        closest, distance, _all_sorted = pick_closest(candidates, captured_lon, captured_lat)

        if closest is not None:
            # 7d: build an update action
            authoritative_oid = closest.get("OBJECTID")
            authoritative_global_id = closest.get("GlobalID")
            authoritative_raw_attrs = authoritative_by_oid.get(authoritative_oid, {}).get(
                "attributes", {}
            )

            update_attrs = build_field_updates(
                captured_attrs, authoritative_raw_attrs, layer_cfg["field_map"], EXCLUDED_FIELDS
            )
            # Identify which authoritative record to update.
            if authoritative_oid is not None:
                update_attrs["OBJECTID"] = authoritative_oid
            if authoritative_global_id is not None:
                update_attrs["GlobalID"] = authoritative_global_id

            # Geometry: use captured geometry as-is. Both layers are fetched
            # with out_sr=4326 (see fetch_all_features), so captured_geometry
            # is already WGS84 and compatible with the authoritative layer.
            update_action = {
                "attributes": update_attrs,
                "geometry": captured_geometry,
            }
            planned_updates.append(
                {
                    **update_action,
                    "_captured_global_id": captured_global_id,
                    "_captured_oid": captured_oid,
                    "_authoritative_oid": authoritative_oid,
                    "_distance": distance,
                }
            )
            planned_rows.append(
                {
                    "captured_global_id": captured_global_id,
                    "action": "would_update",
                    "matched_authoritative_oid": authoritative_oid,
                    "distance_m": distance,
                    "threshold_m": threshold_m,
                }
            )
        else:
            # 7e: build an append action
            append_attrs = {}
            for captured_field, value in captured_attrs.items():
                target_field = layer_cfg["field_map"].get(captured_field, captured_field)
                if target_field in EXCLUDED_FIELDS:
                    continue
                append_attrs[target_field] = value

            # Ensure the type field is populated for self-healing re-discovery.
            # find_candidates reads feature[type_field_authoritative] on the
            # authoritative side, so that's the key that must be correct.
            # Layers with no type matching configured have nothing to populate here.
            if type_field_authoritative and type_field_authoritative not in EXCLUDED_FIELDS:
                append_attrs[type_field_authoritative] = captured_attrs.get(
                    type_field_captured
                )

            append_action = {
                "attributes": append_attrs,
                "geometry": captured_geometry,
            }
            planned_appends.append(
                {
                    **append_action,
                    "_captured_global_id": captured_global_id,
                    "_captured_oid": captured_oid,
                }
            )
            planned_rows.append(
                {
                    "captured_global_id": captured_global_id,
                    "action": "would_append",
                    "matched_authoritative_oid": None,
                    "distance_m": None,
                    "threshold_m": threshold_m,
                }
            )

    report_path = os.path.join(args.report_dir, f"{layer_name}_{run_timestamp}.csv")

    # --- Step 8: dry run ---
    if not args.apply:
        write_report(planned_rows, report_path)
        logger.info(
            "Dry run complete. would_update=%d would_append=%d. Report written to %s",
            len(planned_updates),
            len(planned_appends),
            report_path,
        )
        return

    # --- Step 9: apply ---

    # 9a: back up PRE-EDIT authoritative feature state for every planned update,
    # before any write happens. (authoritative_by_oid was built in step 7, keyed
    # by the raw attributes' OBJECTID.)
    backup_entries = []
    for planned in planned_updates:
        authoritative_oid = planned["_authoritative_oid"]
        raw_authoritative = authoritative_by_oid.get(authoritative_oid)
        if raw_authoritative is not None:
            backup_entries.append(
                {
                    "oid": authoritative_oid,
                    "attributes": raw_authoritative.get("attributes", {}),
                    "geometry": raw_authoritative.get("geometry", {}),
                }
            )

    backup_path = os.path.join(args.backup_dir, f"{layer_name}_{run_timestamp}.json")
    write_backup(backup_entries, backup_path)
    logger.info("Backed up %d pre-edit authoritative features to %s", len(backup_entries), backup_path)

    # 9b: apply updates and appends (batched, one call each)
    update_payloads = [
        {"attributes": p["attributes"], "geometry": p["geometry"]} for p in planned_updates
    ]
    append_payloads = [
        {"attributes": p["attributes"], "geometry": p["geometry"]} for p in planned_appends
    ]

    update_results = apply_updates(authoritative_layer, update_payloads) if update_payloads else []
    append_results = apply_appends(authoritative_layer, append_payloads) if append_payloads else []

    outcome_rows = []
    copy_attachments_enabled = layer_cfg["copy_attachments"]

    # 9c/9d: for updates
    for planned, result in zip(planned_updates, update_results):
        captured_global_id = planned["_captured_global_id"]
        captured_oid = planned["_captured_oid"]
        success = result["success"]
        target_oid = planned["_authoritative_oid"]
        attachments_status = None

        if success and copy_attachments_enabled:
            attachments_status, _already_copied = copy_attachments(
                captured_layer,
                captured_oid,
                authoritative_layer,
                target_oid,
                already_copied=_existing_attachment_names(
                    captured_layer, captured_oid, authoritative_layer, target_oid
                ),
            )
        elif success:
            attachments_status = "0/0"

        ledgered = False
        if success and _attachments_fully_succeeded(attachments_status):
            mark_processed(
                ledger,
                captured_global_id,
                "updated",
                target_oid,
                attachments_status,
                datetime.now().isoformat(),
            )
            ledgered = True

        outcome_rows.append(
            {
                "captured_global_id": captured_global_id,
                # rollback.py filters on exactly "updated"/"appended" and reads
                # the OID from "authoritative_oid" — keep these names in sync
                # with conflate/rollback.py's expected report schema.
                "action": "updated",
                "authoritative_oid": target_oid,
                "distance_m": planned["_distance"],
                "threshold_m": threshold_m,
                "success": success,
                "error": result["error"],
                "attachments_status": attachments_status,
                "ledgered": ledgered,
            }
        )

    # 9c/9d: for appends
    for planned, result in zip(planned_appends, append_results):
        captured_global_id = planned["_captured_global_id"]
        captured_oid = planned["_captured_oid"]
        success = result["success"]
        target_oid = result["result_oid"]
        attachments_status = None

        if success and copy_attachments_enabled:
            attachments_status, _already_copied = copy_attachments(
                captured_layer,
                captured_oid,
                authoritative_layer,
                target_oid,
                already_copied=_existing_attachment_names(
                    captured_layer, captured_oid, authoritative_layer, target_oid
                ),
            )
        elif success:
            attachments_status = "0/0"

        ledgered = False
        if success and _attachments_fully_succeeded(attachments_status):
            mark_processed(
                ledger,
                captured_global_id,
                "created",
                target_oid,
                attachments_status,
                datetime.now().isoformat(),
            )
            ledgered = True

        outcome_rows.append(
            {
                "captured_global_id": captured_global_id,
                "action": "appended",
                "authoritative_oid": target_oid,
                "distance_m": None,
                "threshold_m": threshold_m,
                "success": success,
                "error": result["error"],
                "attachments_status": attachments_status,
                "ledgered": ledgered,
            }
        )

    # 9e: write the SAME-timestamped report with actual outcomes
    write_report(outcome_rows, report_path)

    # 9f: save ledger
    save_ledger(ledger_path, ledger)

    n_updates_ok = sum(1 for r in update_results if r["success"])
    n_appends_ok = sum(1 for r in append_results if r["success"])
    logger.info(
        "Apply run complete. updates=%d/%d succeeded, appends=%d/%d succeeded. "
        "Backup=%s Report=%s",
        n_updates_ok,
        len(update_results),
        n_appends_ok,
        len(append_results),
        backup_path,
        report_path,
    )


def _existing_attachment_names(source_layer, source_oid, target_layer, target_oid) -> set:
    """Return the set of attachment names from the source that already exist on
    the target, i.e. the ones a prior (partially-failed) copy attempt already
    succeeded on.

    Used to seed copy_attachments' already_copied set on retries, so those
    attachments aren't re-uploaded. Intersected with the source's own
    attachment names (rather than returning all target names) because the
    authoritative/target feature may have pre-existing attachments of its
    own that were never part of this copy — including those in the seed
    would inflate copy_attachments' "n/n" success count past the source's
    total and make the "n/n" ledger check permanently fail.

    If listing fails for any reason, falls back to an empty set (worst case:
    a harmless re-upload attempt).
    """
    try:
        source_names = {
            a.get("name") for a in source_layer.attachments.get_list(source_oid) if a.get("name")
        }
        target_names = {
            a.get("name") for a in target_layer.attachments.get_list(target_oid) if a.get("name")
        }
    except Exception:
        return set()
    return source_names & target_names


def _attachments_fully_succeeded(status: str) -> bool:
    """Parse an attachment status string like "2/3" and return whether it's fully successful.

    "0/0" (no attachments to copy) counts as full success.
    """
    if not status:
        return False
    try:
        copied_str, total_str = status.split("/")
        copied, total = int(copied_str), int(total_str)
    except (ValueError, AttributeError):
        return False
    return copied == total


if __name__ == "__main__":
    main()
