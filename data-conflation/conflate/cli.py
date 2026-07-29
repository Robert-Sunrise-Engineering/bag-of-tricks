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
import json
import logging
import os
from datetime import datetime

from conflate.config import load_config, load_local_config, validate_layer_config
from conflate.gis_client import (
    connect,
    get_layer,
    validate_schema,
    validate_capabilities,
    validate_geometry_type,
)
from conflate.paging import fetch_all_features
from conflate.threshold import format_threshold_both_units
from conflate.matching import assign_matches
from conflate.nullfill import build_field_updates
from conflate.ledger import load_ledger, save_ledger, mark_processed, is_processed
from conflate.report import write_report
from conflate.backup import write_backup
from conflate.attachments import copy_attachments, target_attachment_name
from conflate.apply import apply_updates, apply_appends
from conflate.fields import EXCLUDED_FIELDS

logger = logging.getLogger(__name__)


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


def _has_point_geometry(simplified_feature: dict) -> bool:
    """True if a feature simplified by ``_simplify_feature`` has a usable
    point location (lon and lat both non-None).

    False for features with no geometry at all, or a non-point geometry
    (line/polygon geometries have no "x"/"y" keys, so ``_simplify_feature``
    leaves lon/lat as None for them). Used to filter such features out
    before they reach geodesic_distance, which raises on None inputs.
    """
    return simplified_feature["lon"] is not None and simplified_feature["lat"] is not None


def _seed_claimed_oids(ledger: dict) -> set:
    """Return the set of authoritative OIDs already claimed by a prior run,
    per the ledger's recorded ``authoritative_oid`` for each processed
    captured feature.

    Used to seed a run's one-to-one-matching guard so an authoritative
    record claimed by a captured feature in a prior run stays off-limits to
    a different, newly-captured feature this run -- not just within a
    single run.
    """
    return {
        entry["authoritative_oid"]
        for entry in ledger.values()
        if entry.get("authoritative_oid") is not None
    }


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
        "--force",
        action="store_true",
        default=False,
        help="With --rollback: proceed even if the backup/report file has no "
        "recorded layer identity (predates the layer-mismatch safety check) "
        "and so can't be verified against --layer. Only use this after "
        "manually confirming the backup/report really were produced for "
        "--layer's value. Has no effect otherwise.",
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


def _rollback_log_path(backup_path: str, report_dir: str, rollback_timestamp: str) -> str:
    """Derive this rollback run's own audit log path from the backup it's
    rolling back and the rollback's own timestamp.

    Written alongside reports (no separate directory/CLI flag), named
    <layer>_<apply_timestamp>_rollback_<rollback_timestamp>.json so it sorts
    next to the run it's undoing and a re-run rollback doesn't collide, e.g.
    backups/hydrants_20260725_100000.json, "20260725_143000" ->
    reports/hydrants_20260725_100000_rollback_20260725_143000.json
    """
    base = os.path.basename(backup_path)
    stem, _ext = os.path.splitext(base)
    return os.path.join(report_dir, f"{stem}_rollback_{rollback_timestamp}.json")


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

    ledger_path = os.path.join("state", f"{args.layer}.json")

    rollback_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _rollback_log_path(backup_path, args.report_dir, rollback_timestamp)

    logger.info(
        "Rolling back layer '%s' using backup=%s report=%s",
        args.layer,
        backup_path,
        report_path,
    )
    rollback(
        backup_path,
        report_path,
        authoritative_layer,
        ledger_path,
        expected_layer_name=args.layer,
        force=args.force,
        log_path=log_path,
    )
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
    validate_geometry_type(captured_layer)
    validate_geometry_type(authoritative_layer)

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
    # Features with no geometry (or a non-point geometry, which has no x/y)
    # yield lon/lat of None; excluded here rather than left in so a single
    # bad/missing authoritative geometry can't reach geodesic_distance (which
    # raises on None) and abort matching for every captured feature.
    authoritative_simplified = [
        f for f in (_simplify_feature(raw) for raw in authoritative_features_raw)
        if _has_point_geometry(f)
    ]
    n_authoritative_no_geometry = len(authoritative_features_raw) - len(authoritative_simplified)
    if n_authoritative_no_geometry:
        logger.warning(
            "Skipping %d authoritative feature(s) with no usable point geometry "
            "(missing geometry, or a non-point geometry type).",
            n_authoritative_no_geometry,
        )
    # Map OBJECTID -> raw (unsimplified) authoritative feature, for real attribute
    # access (build_field_updates, backups) without the injected lon/lat keys.
    authoritative_by_oid = {
        f.get("attributes", {}).get("OBJECTID"): f for f in authoritative_features_raw
    }

    # --- Step 7: plan actions for each unprocessed captured feature ---
    planned_updates = []  # each: {"attributes":..., "geometry":..., "_captured_global_id":..., "_captured_oid":..., "_authoritative_oid":..., "_distance":..., "_candidates_json":..., "_assignment_overridden_nearest":...}
    planned_appends = []  # each: {"attributes":..., "geometry":..., "_captured_global_id":..., "_captured_oid":..., "_candidates_json":..., "_assignment_overridden_nearest":...}
    planned_rows = []  # for the report

    # Authoritative OIDs already matched by a captured feature, this run OR a
    # prior one. Enforces one-to-one matching: once claimed, a candidate is no
    # longer eligible to be picked as the closest match for a later captured
    # feature (prevents two captured features from both updating the same
    # authoritative record). Seeded from the ledger (which records the
    # authoritative_oid each already-processed captured feature was matched
    # to) so the invariant holds across runs, not just within one -- otherwise
    # a newly-captured feature within threshold of an already-claimed record
    # could re-claim it on a later run and silently merge into it instead of
    # being appended as its own feature.
    claimed_authoritative_oids = _seed_claimed_oids(ledger)

    # 7a: filter to unprocessed, point-geometry captured features. Everything
    # else is either skipped outright (already processed) or reported directly
    # as skipped_no_geometry. valid_items pairs each surviving raw feature with
    # its simplified form, in the same order assign_matches will index its
    # per-row results by below.
    valid_items = []
    for raw_captured in captured_features_raw:
        captured_attrs = raw_captured.get("attributes", {})
        captured_global_id = captured_attrs.get("GlobalID")

        if is_processed(ledger, captured_global_id):
            continue

        captured_simplified = _simplify_feature(raw_captured)

        if not _has_point_geometry(captured_simplified):
            # No usable point geometry (missing, or a non-point geometry
            # type with no x/y) -- skip rather than let geodesic_distance
            # raise on None and abort the whole run over one bad feature.
            logger.warning(
                "Skipping captured feature with no usable point geometry "
                "(captured_global_id=%s)",
                captured_global_id,
            )
            planned_rows.append(
                {
                    "captured_global_id": captured_global_id,
                    "action": "skipped_no_geometry",
                    "matched_authoritative_oid": None,
                    "distance_m": None,
                    "threshold_m": threshold_m,
                    "candidates_json": json.dumps([]),
                    "assignment_overridden_nearest": False,
                    "layer": layer_name,
                }
            )
            continue

        valid_items.append((raw_captured, captured_simplified))

    # 7b/7c: solve the whole batch at once as a global optimal assignment,
    # instead of resolving each captured feature's match independently and
    # greedily -- see docs/2026-07-28-global-optimal-matching-design.md for
    # why greedy nearest-unclaimed can silently mismatch a
    # systematically-offset cluster. match_results is indexed the same way as
    # valid_items (list index, not GlobalID -- GlobalID isn't guaranteed
    # non-null, index is unique by construction).
    unclaimed_authoritative = [
        f for f in authoritative_simplified
        if f.get("OBJECTID") not in claimed_authoritative_oids
    ]
    match_results = assign_matches(
        [simplified for _, simplified in valid_items],
        unclaimed_authoritative,
        type_field_authoritative,
        type_field_captured,
        threshold_m,
    )

    for i, (raw_captured, captured_simplified) in enumerate(valid_items):
        captured_attrs = raw_captured.get("attributes", {})
        captured_geometry = raw_captured.get("geometry") or {}
        captured_global_id = captured_attrs.get("GlobalID")
        captured_oid = captured_attrs.get("OBJECTID")

        result = match_results[i]
        candidates_json = json.dumps(result["candidates"])
        assignment_overridden_nearest = result["assignment_overridden_nearest"]

        if result["matched"]:
            # 7d: build an update action
            authoritative = result["authoritative_feature"]
            distance = result["distance_m"]
            authoritative_oid = authoritative.get("OBJECTID")
            claimed_authoritative_oids.add(authoritative_oid)
            authoritative_global_id = authoritative.get("GlobalID")
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
                    "_candidates_json": candidates_json,
                    "_assignment_overridden_nearest": assignment_overridden_nearest,
                }
            )
            planned_rows.append(
                {
                    "captured_global_id": captured_global_id,
                    "action": "would_update",
                    "matched_authoritative_oid": authoritative_oid,
                    "distance_m": distance,
                    "threshold_m": threshold_m,
                    "candidates_json": candidates_json,
                    "assignment_overridden_nearest": assignment_overridden_nearest,
                    "layer": layer_name,
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
                    "_candidates_json": candidates_json,
                    "_assignment_overridden_nearest": assignment_overridden_nearest,
                }
            )
            planned_rows.append(
                {
                    "captured_global_id": captured_global_id,
                    "action": "would_append",
                    "matched_authoritative_oid": None,
                    "distance_m": None,
                    "threshold_m": threshold_m,
                    "candidates_json": candidates_json,
                    "assignment_overridden_nearest": assignment_overridden_nearest,
                    "layer": layer_name,
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
    write_backup(backup_entries, backup_path, layer_name, layer_cfg["authoritative_url"])
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

    def _build_outcome_row(planned, result, *, action_label, ledger_action, target_oid, distance_m):
        """Copy attachments (if enabled), ledger the feature if fully
        successful, and build its outcome row -- shared by the update and
        append loops below, which differ only in target_oid derivation,
        action labels, and whether distance_m applies.
        """
        captured_global_id = planned["_captured_global_id"]
        captured_oid = planned["_captured_oid"]
        success = result["success"]
        attachments_status = None
        added_attachment_ids = []

        if success and copy_attachments_enabled:
            attachments_status, _already_copied_names, added_attachment_ids = copy_attachments(
                captured_layer,
                captured_oid,
                authoritative_layer,
                target_oid,
                captured_global_id,
                already_copied=_existing_attachment_names(
                    captured_layer, captured_oid, authoritative_layer, target_oid, captured_global_id
                ),
            )
        elif success:
            attachments_status = "0/0"

        ledgered = False
        if success and _attachments_fully_succeeded(attachments_status):
            mark_processed(
                ledger,
                captured_global_id,
                ledger_action,
                target_oid,
                attachments_status,
                datetime.now().isoformat(),
            )
            ledgered = True

        return {
            "captured_global_id": captured_global_id,
            # rollback.py filters on exactly "updated"/"appended" and reads
            # the OID from "authoritative_oid" — keep these names in sync
            # with conflate/rollback.py's expected report schema.
            "action": action_label,
            "authoritative_oid": target_oid,
            "distance_m": distance_m,
            "threshold_m": threshold_m,
            "success": success,
            "error": result["error"],
            "attachments_status": attachments_status,
            "attachments_added": json.dumps(added_attachment_ids),
            "ledgered": ledgered,
            "candidates_json": planned.get("_candidates_json"),
            "assignment_overridden_nearest": planned.get("_assignment_overridden_nearest"),
            "layer": layer_name,
        }

    # 9c/9d: for updates
    for planned, result in zip(planned_updates, update_results):
        outcome_rows.append(
            _build_outcome_row(
                planned,
                result,
                action_label="updated",
                ledger_action="updated",
                target_oid=planned["_authoritative_oid"],
                distance_m=planned["_distance"],
            )
        )

    # 9c/9d: for appends
    for planned, result in zip(planned_appends, append_results):
        outcome_rows.append(
            _build_outcome_row(
                planned,
                result,
                action_label="appended",
                ledger_action="created",
                target_oid=result["result_oid"],
                distance_m=None,
            )
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


def _existing_attachment_names(
    source_layer, source_oid, target_layer, target_oid, captured_global_id
) -> set:
    """Return the set of expected target-side attachment names (per
    target_attachment_name) for source attachments that already exist on the
    target, i.e. the ones a prior (partially-failed) copy attempt already
    succeeded on.

    Used to seed copy_attachments' already_copied set on retries, so those
    attachments aren't re-uploaded. Computed as each source attachment's
    collision-proof expected target name intersected with the target's actual
    attachment names — never just the target's own names — because the
    authoritative/target feature may have pre-existing attachments of its own
    that were never part of this copy (a generically-named leftover attachment,
    for instance) and including those in the seed would both inflate
    copy_attachments' "n/n" success count and, more importantly, cause it to
    skip a genuinely new upload it mistakes for one already done.

    If listing fails for any reason, falls back to an empty set (worst case:
    a harmless re-upload attempt).
    """
    try:
        source_attachments = source_layer.attachments.get_list(source_oid)
        target_names = {
            a.get("name") for a in target_layer.attachments.get_list(target_oid) if a.get("name")
        }
    except Exception:
        return set()

    expected_target_names = {
        target_attachment_name(captured_global_id, a.get("id"), a.get("name"))
        for a in source_attachments
        if a.get("name") and a.get("id")
    }
    return expected_target_names & target_names


def _attachments_fully_succeeded(status: str | None) -> bool:
    """Parse an attachment status string like "2/3" and return whether it's fully successful.

    "0/0" (no attachments to copy) counts as full success. None -- returned by
    copy_attachments when the source attachment list couldn't even be
    retrieved -- is not a "n/n" string and so correctly falls through to False.
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
