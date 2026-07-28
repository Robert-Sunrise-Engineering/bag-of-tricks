# Reference

A function-by-function map of the `conflate/` package: what each function
takes, what it returns, and what side effects it has (network calls to
ArcGIS Online, file I/O). This is a lookup document, not a tutorial — for
"how do I run this tool," see [`../README.md`](../README.md).

Implementation details (algorithms, internal control flow) are deliberately
omitted except where they're load-bearing for correct use (e.g. batch sizes,
which string values mean what).

## Architecture overview

`conflate_main.py` is a four-line shim that imports and calls
`conflate.cli.main()`. `cli.py` is the only orchestrator — it contains no
matching/threshold/edit logic of its own, only sequencing calls into every
other module:

**Normal run (dry run or `--apply`), in order:**

1. `config.load_config` / `load_local_config` / `validate_layer_config` — load and validate configuration.
2. `gis_client.connect` → `get_layer` (both layers) → `validate_schema` / `validate_capabilities` / `validate_geometry_type` — connect to AGOL and fail fast on schema/capability/geometry problems.
3. `paging.fetch_all_features` — bulk-fetch every feature from both layers, once each, always reprojected to WGS84 (`out_sr=4326`).
4. `ledger.load_ledger` — load the layer's ledger; seed the cross-run "already claimed" authoritative OID set (`cli._seed_claimed_oids`) and the "already processed" captured-feature check (`ledger.is_processed`).
5. Per unprocessed captured feature: `matching.find_candidates` (spatial + optional type filter, via `geometry.geodesic_distance` internally) → `matching.pick_closest_unclaimed` → either build an update via `nullfill.build_field_updates` (filtered by `fields.EXCLUDED_FIELDS`) or build an append.
6. `threshold.format_threshold_both_units` — human-readable threshold for logging only.
7. **Dry run**: `report.write_report` writes a "would_*" CSV. Stop here.
8. **Apply**: `backup.write_backup` snapshots pre-edit authoritative state for every planned update, *before* any write. `apply.apply_updates` / `apply_appends` perform the actual AGOL writes. `attachments.copy_attachments` copies files (if configured) and `ledger.mark_processed` records success — but only if the write *and* (if enabled) the attachment copy both fully succeeded. `report.write_report` writes the outcomes CSV (this is what `rollback.py` later reads). `ledger.save_ledger` persists the ledger.

**Rollback (`--rollback <backup-file>`):** `cli._do_rollback` derives the
paired report path and a new rollback-log path, connects to AGOL, and
delegates entirely to `rollback.rollback()`, which reads the backup
(`backup.load_backup_meta`) and report CSV, checks layer identity
(`LayerMismatchError` guard via `_check_layer_match`), restores `"updated"`
rows via `apply.apply_updates` (attributes filtered by
`fields.EXCLUDED_FIELDS`) and deletes `"appended"` rows directly via
`layer.edit_features(deletes=...)`, deletes attachments added during the run
via `attachments.delete_attachments_batch`, verifies the restored state
against live AGOL via `verify.verify_restore`, clears the corresponding
ledger entries, and writes a full audit log via `run_log.write_rollback_log`.

`fields.EXCLUDED_FIELDS` is the one constant shared across all three
write-building code paths (`cli.py`'s update/append payloads,
`rollback.py`'s restore payloads, `verify.py`'s comparison filtering),
guaranteeing AGOL-managed system fields are never written or flagged as
mismatches anywhere in the tool.

---

## `conflate/cli.py` — orchestration / entry point

The only module that sequences calls into every other module. In this file
nearly all the actual behavior lives in the underscore-prefixed helper
functions below, not in `main()` itself — `main()` mostly just calls them in
order.

- **`_simplify_feature(raw_feature: dict) -> dict`** — Flattens an AGOL
  feature (`{"attributes": {...}, "geometry": {...}}`) into one dict: all
  attributes merged in, plus injected `lon`/`lat` keys from
  `geometry["x"]`/`["y"]`. Since `fetch_all_features` always queries with
  `out_sr=4326`, `lon`/`lat` are always WGS84 degrees when present. No side
  effects.
- **`_has_point_geometry(simplified_feature: dict) -> bool`** — `True` iff
  both `lon` and `lat` are non-`None`. `False` for features with missing
  geometry or a non-point geometry (lines/polygons have no `x`/`y`, so
  `_simplify_feature` leaves `lon`/`lat` as `None`). Used to filter such
  features out before they'd reach `geodesic_distance`, which raises on
  `None` input.
- **`_seed_claimed_oids(ledger: dict) -> set`** — Returns the set of
  `authoritative_oid` values recorded across all ledger entries. Used to
  seed the one-to-one matching guard so an authoritative record claimed in a
  *prior* run stays off-limits this run too, not just within a single run.
- **`_build_arg_parser() -> argparse.ArgumentParser`** — Builds the CLI's
  argument parser. See the README's flag table for the full flag list
  (`--layer`, `--apply`, `--rollback`, `--force`, `--backup-dir`,
  `--report-dir`).
- **`_report_path_for_backup(backup_path: str, report_dir: str) -> str`** —
  Derives a run's report CSV path from its backup JSON path (same
  `<layer>_<timestamp>` stem, `report_dir` instead of the backup's
  directory).
- **`_rollback_log_path(backup_path: str, report_dir: str, rollback_timestamp: str) -> str`**
  — Derives a rollback run's own audit-log path:
  `<report_dir>/<layer>_<apply_ts>_rollback_<rollback_ts>.json`.
- **`_do_rollback(args) -> None`** — Loads config, connects to AGOL, resolves
  the backup/report/ledger/log paths, and delegates to
  `conflate.rollback.rollback(...)`. **Side effects:** network (AGOL
  connect), reads config files, delegates all further I/O to `rollback()`.
- **`main() -> None`** — The full CLI workflow described in the architecture
  overview above (dry run or `--apply`, or rollback dispatch). **Side
  effects:** network calls to AGOL (query/edit/attachments), reads
  `config.json`/`config.local.json`, reads/writes the ledger JSON, writes
  backup JSON (apply only), writes the report CSV.
- **`_existing_attachment_names(source_layer, source_oid, target_layer, target_oid, captured_global_id) -> set`**
  — Computes which target-side attachment names (per the deterministic
  naming scheme — see `attachments.target_attachment_name`) already exist on
  the target feature, so a retry doesn't re-upload attachments a prior
  (partially-failed) attempt already copied. Only counts a name as "already
  copied" if it's both an *expected* name (derived from a real source
  attachment) *and* actually present on the target — never just "whatever's
  currently on the target," since the target may have unrelated pre-existing
  attachments of its own. **Side effects:** network calls
  (`attachments.get_list` on both layers); any exception is swallowed and an
  empty set is returned (worst case: a harmless re-upload attempt).
- **`_attachments_fully_succeeded(status: str | None) -> bool`** — Parses a
  `"copied/total"` string (e.g. `"2/3"`) and returns whether `copied ==
  total`. `"0/0"` (genuinely zero attachments) counts as success. `None`
  (returned by `attachments.copy_attachments` when even *listing* source
  attachments failed) is not a valid `"n/n"` string and correctly returns
  `False`.
- **`main()`'s internal `_build_outcome_row` closure** — defined *inside*
  `main()`, not an independently callable module-level function. Shared by
  the update and append result loops: copies attachments if the write
  succeeded and `copy_attachments` is enabled, ledgers the feature only if
  fully successful, and builds its report row. See [Artifact
  schemas](#artifact-schemas) for the exact row shape it produces.

## `conflate/config.py` — config loading/validation

- **`load_config(path) -> dict`** — Reads/parses a JSON file (`config.json`
  in practice). **Raises:** `FileNotFoundError`, `json.JSONDecodeError`.
  **Side effect:** file read.
- **`load_local_config(path) -> dict`** — Reads/parses `config.local.json`.
  Same error behavior/side effects as `load_config`.
- **`validate_layer_config(layer_cfg: dict) -> None`** — Confirms all
  required keys (`authoritative_url`, `captured_url`, `match_threshold_m`,
  `field_map`, `copy_attachments`) are present, and that
  `type_field_authoritative`/`type_field_captured` are given together or not
  at all. **Raises:** `ValueError` naming the missing/mismatched key(s).

## `conflate/gis_client.py` — AGOL connection & layer validation

- **`connect(local_config: dict) -> GIS`** — Authenticates to
  ArcGIS Online/Portal. Dispatches on `local_config["auth_type"]` (default
  `"builtin"`): `"builtin"` does plain username/password login;
  `"oauth"` does client-ID + cached-profile OAuth (interactive browser login
  only the first time a given `profile` is used; silently cached/refreshed
  after that). **Raises:** `ValueError` if `auth_type == "oauth"` and
  `client_id`/`profile` are missing, or if `auth_type` is unrecognized; a
  cached-profile OAuth failure is re-raised with a message pointing at a
  likely-expired refresh token (since an unattended run can't complete an
  interactive login page). **Side effect:** network auth call; may open a
  browser (oauth, first use) or read/write a cached token profile.
- **`get_layer(gis: GIS, url: str) -> FeatureLayer`** — Thin wrapper around
  `FeatureLayer(url, gis=gis)`. No network call itself (lazy); later
  property access on the returned object does make calls.
- **`validate_schema(layer: FeatureLayer, required_fields: list[str]) -> None`**
  — Confirms every field in `required_fields` exists on `layer` (handles
  both dict- and object-shaped field metadata from the `arcgis` SDK).
  **Raises:** `ValueError` listing missing fields. **Side effect:** network
  call via `layer.properties`.
- **`validate_capabilities(layer: FeatureLayer, copy_attachments: bool) -> None`**
  — Raises if `copy_attachments=True` but the layer doesn't have attachments
  enabled, or if the layer's capabilities string is missing `"Create"` or
  `"Update"`. **Raises:** `ValueError`. **Side effect:** network call via
  `layer.properties`.
- **`validate_geometry_type(layer: FeatureLayer) -> None`** — Raises unless
  `layer.properties["geometryType"] == "esriGeometryPoint"` — matching only
  supports point layers; a line/polygon layer would otherwise silently
  produce `None` lon/lat deep inside `_simplify_feature` and crash later with
  an opaque error. **Raises:** `ValueError`. **Side effect:** network call.

## `conflate/paging.py` — bulk fetch

- **`fetch_all_features(layer) -> list[dict]`** — Fetches every feature via
  `layer.query(where="1=1", out_fields="*", return_all_records=True,
  out_sr=4326)`, converts each to a plain dict, and cross-checks the fetched
  count against a separate `return_count_only=True` query. Forces WGS84
  reprojection for every downstream consumer regardless of the layer's
  native spatial reference — nothing downstream ever reprojects, they all
  assume `out_sr=4326` already happened here. **Raises:** `RuntimeError` on
  a count mismatch (paging may have truncated results). **Side effect:** two
  network queries.

## `conflate/geometry.py` — coordinate math (pure)

- **`geodesic_distance(lon1, lat1, lon2, lat2) -> float`** — Ellipsoidal
  (WGS84) great-circle distance in meters via `pyproj.Geod.inv`. No side
  effects.
- **`reproject_point(x, y, from_wkid, to_wkid) -> tuple`** — Reprojects a
  point between two EPSG/WKID coordinate reference systems via
  `pyproj.Transformer`. No side effects. **Not currently called anywhere**
  in `cli.py`, `rollback.py`, or `verify.py` — every query in the codebase
  requests `out_sr=4326` directly instead, so this function is a standalone
  utility, not part of the active pipeline. Don't assume it's wired into
  anything.

## `conflate/matching.py` — pure matching logic

- **`pick_closest(candidates: list[dict], captured_lon: float, captured_lat: float) -> tuple`**
  — Computes geodesic distance from the captured point to every candidate
  (each candidate dict needs `"lon"`/`"lat"` keys), sorts ascending. Returns
  `(closest_candidate, closest_distance, all_candidates_with_distances_sorted)`
  — the third element is a list of `(candidate, distance)` tuples. Returns
  `(None, None, [])` if `candidates` is empty. No side effects.
- **`pick_closest_unclaimed(candidates, captured_lon, captured_lat, claimed_oids: set) -> tuple`**
  — Same as `pick_closest`, but skips any candidate whose `OBJECTID` is in
  `claimed_oids`. Returns `(closest_unclaimed_candidate,
  closest_unclaimed_distance, all_candidates_with_distances_sorted)` — the
  first two are `(None, None)` if every candidate is claimed; the third
  element is always the *full*, unfiltered sorted list regardless of claims.
  No side effects.
- **`find_candidates(authoritative_features, captured_feature, type_field_authoritative, type_field_captured, threshold_m) -> list[dict]`**
  — Filters `authoritative_features` to those within `threshold_m` meters of
  `captured_feature`'s coordinates. If both type-field arguments are given
  (non-`None`), also requires the type value to match exactly; if either is
  `None`, type is ignored and matching is purely spatial. Returned list
  order is unspecified. No side effects.

## `conflate/nullfill.py` — pure field-merge logic

- **`is_null(value) -> bool`** — `True` for `None` or a whitespace-only
  string. `False` for everything else, **including** `0`, `0.0`, and
  `False` — a falsy-but-meaningful value is never treated as null.
- **`build_field_updates(captured_attrs: dict, authoritative_attrs: dict, field_map: dict, excluded_fields: set) -> dict`**
  — Builds the update payload for an existing authoritative record. Effective
  field pairs considered: every `(captured_field → authoritative_field)` in
  `field_map`, plus any same-named field present in both dicts that isn't
  already a `field_map` key. For each pair, includes
  `authoritative_field: captured_value` in the result **only if**: the
  authoritative value is currently null (per `is_null`), the captured value
  is not null, and `authoritative_field` is not in `excluded_fields`. This is
  the tool's "never overwrite existing non-null data" semantics. No side
  effects.

## `conflate/fields.py` — shared constants

- **`EXCLUDED_FIELDS: set`** — `{"OBJECTID", "GlobalID", "Shape", "SHAPE",
  "Creator", "CreationDate", "Editor", "EditDate"}`. System/editor-managed
  fields AGOL controls itself, which must never be written by null-fill,
  append, or restore payloads, and are never flagged as mismatches during
  verification. The single source of truth shared by `cli.py`, `rollback.py`,
  and `verify.py`.

## `conflate/apply.py` — write operations

- **`apply_updates(layer, updates: list[dict]) -> list[dict]`** — Calls
  `layer.edit_features(updates=updates)` in one batched call (each update
  dict shaped `{"attributes": {...}, "geometry": {...}}`, attributes must
  include `OBJECTID` or `GlobalID`), then normalizes AGOL's `updateResults`
  into one result dict per input:
  `{"input_ref", "success", "result_oid", "error"}` (`result_oid` is
  `objectId` or `globalId` from AGOL's response; `error` is `None` unless
  `success` is falsy and AGOL returned one). **Side effect:** one batched
  network write.
- **`apply_appends(layer, adds: list[dict]) -> list[dict]`** — Same pattern
  via `layer.edit_features(adds=adds)`, normalizing `addResults` into the
  same result shape. **Side effect:** one batched network write.

## `conflate/attachments.py` — attachment copy/delete

- **`target_attachment_name(captured_global_id, attachment_id, original_name) -> str`**
  — Builds a deterministic, collision-proof target-side filename:
  `"<captured_global_id-no-braces-no-dashes>_att<attachment_id>_<original stem><original suffix>"`.
  Guarantees a generic source filename (e.g. a device default like
  `"Photo1.jpg"`) can never collide with another attachment on the target,
  and that re-running a copy for the same source attachment always produces
  the same name (which is what makes retry-dedup by name safe). No side
  effects.
- **`copy_attachments(source_layer, source_oid, target_layer, target_oid, captured_global_id, already_copied=None) -> tuple[str | None, set, list]`**
  — Lists source attachments, skips any whose target name is already in
  `already_copied`, downloads each remaining one to a temp directory, renames
  it to its collision-proof target name, and uploads it to the target.
  Returns `(status, updated_names_set, newly_added_target_attachment_ids)`
  where `status` is `"<copied>/<total>"` (e.g. `"2/3"`), `"0/0"` if the
  source genuinely has zero attachments, or **`None`** if the source
  attachment list couldn't even be retrieved — `None` is deliberately
  distinct from `"0/0"` (see `cli._attachments_fully_succeeded`: only an
  exact `"n/n"` string counts as full success, so `None` correctly leaves
  the feature unledgered for retry rather than being mistaken for "nothing
  to copy"). **Side effects:** network downloads/uploads; local temp-directory
  file I/O (created via `tempfile.mkdtemp`, always cleaned up via
  `shutil.rmtree` in a `finally` block); logging.
- **`delete_attachments(target_layer, target_oid, attachment_ids_to_delete) -> dict`**
  — Calls `target_layer.attachments.delete` once per ID and parses AGOL's
  actual `deleteAttachmentResults` response (rather than assuming success
  whenever no exception was raised — a server-side failure reported without
  an exception is still caught). Returns `{"success": bool, "errors": [str,
  ...], "results": [{"attachment_id", "success", "error"}, ...]}` — one
  `results` entry per input ID, falling back to `error: "unparsed response"`
  if AGOL's response doesn't have the expected shape. **Side effect:**
  network delete calls, one per attachment ID.
- **`delete_attachments_batch(layer, oid_to_ids: dict) -> list[dict]`** —
  Calls `delete_attachments` once per authoritative OID in `oid_to_ids`
  (`{authoritative_oid: [attachment_id, ...]}`), tagging each result dict
  with `"authoritative_oid"`. Skips OIDs with an empty ID list. **Side
  effect:** network delete calls (used by `rollback.py`).

## `conflate/backup.py` — pre-write snapshot format

- **`write_backup(features: list[dict], path, layer: str, authoritative_url: str) -> None`**
  — Serializes `{"layer": layer, "authoritative_url": authoritative_url,
  "entries": features}` as JSON to `path`. Each entry in `features` is
  expected shaped `{"oid": int, "attributes": {...}, "geometry": {...}}`
  — the pre-edit state of one authoritative feature, captured **before**
  any write. **Side effect:** file write; creates parent directories.
- **`load_backup_meta(path) -> dict`** — Reads/parses the backup JSON,
  returning the full `{"layer", "authoritative_url", "entries"}` dict.
  Backward-compatible with legacy backups that are a bare JSON list (from
  before layer identity was tracked): those are wrapped as `{"layer": None,
  "authoritative_url": None, "entries": <the list>}`. **Side effect:** file
  read; propagates `FileNotFoundError`/`json.JSONDecodeError`.
- **`load_backup(path) -> list[dict]`** — Convenience wrapper returning just
  `load_backup_meta(path)["entries"]`.

## `conflate/ledger.py` — processed-feature tracking (state)

- **`load_ledger(path: str) -> dict`** — Reads the ledger JSON at `path`;
  returns `{}` (no raise) if the file doesn't exist. **Side effect:** file
  read.
- **`save_ledger(path: str, ledger: dict) -> None`** — Writes `ledger` as
  pretty-printed JSON. **Side effect:** file write; creates parent
  directories.
- **`mark_processed(ledger, captured_global_id, action, authoritative_oid, attachments_status, run_time) -> None`**
  — Mutates `ledger` **in place**: sets
  `ledger[captured_global_id] = {"action", "authoritative_oid",
  "attachments_status", "run_time"}`. No return value, no file I/O itself
  (caller must call `save_ledger` separately to persist).
- **`is_processed(ledger: dict, captured_global_id: str) -> bool`** — Simple
  membership check (`captured_global_id in ledger`).

## `conflate/report.py` — CSV report writer

- **`write_report(rows: list[dict], path) -> None`** — Writes `rows` as CSV
  via `csv.DictWriter`. The header is the **union** of all keys across every
  row (in first-seen order); a row missing a key gets `""` for that column.
  Writes an empty file (no crash) if `rows` is empty. **Side effect:** file
  write; creates parent directories.

## `conflate/threshold.py` — formatting helper (pure)

- **`format_threshold_both_units(value: float, source_units: str) -> str`**
  — Formats a threshold showing both units, e.g. `"10.67 m (35.01 ft)"` or
  `"12.00 ft (3.66 m)"`. `source_units` must be `"meters"` or `"feet"`
  (case-insensitive). **Raises:** `ValueError` for any other value.

## `conflate/rollback.py` — undo a prior apply run

- **`class LayerMismatchError(Exception)`** — Raised when `--layer` doesn't
  match the layer identity recorded in a backup/report.
- **`_check_layer_match(backup_meta, report_rows, expected_layer_name, force=False) -> None`**
  — The entire layer-mismatch safety guard. Raises `LayerMismatchError` if
  the backup's recorded `layer` or any report row's `layer` column disagrees
  with `expected_layer_name`. If **neither** file has a recorded layer
  identity at all (a legacy backup/report from before this check existed),
  it also raises **unless `force=True`** — that exact ambiguity (no way to
  verify `--layer` against the files) is what previously allowed a `--layer`
  typo to silently roll back the wrong live layer, so it fails closed by
  default.
- **`rollback(backup_path, report_path, layer, ledger_path, *, expected_layer_name=None, force=False, log_path=None) -> None`**
  — The full rollback workflow. `layer` is an already-connected AGOL
  `FeatureLayer` object (not a config key). Behavior:
  1. Reads the report CSV and backup JSON (`backup.load_backup_meta`).
  2. Runs `_check_layer_match` if `expected_layer_name` is given.
  3. Queries the live feature count (`count_before`), taken before any write.
  4. For report rows with `action == "updated"`: builds a restore payload
     from the matching backup entry (attributes minus `fields.EXCLUDED_FIELDS`,
     `OBJECTID` re-added to identify the record, backup's geometry), and
     applies all of them via one `apply.apply_updates` call. Rows with an
     unparsable `authoritative_oid` or no matching backup entry are skipped
     with a warning, not fatal.
  5. For rows with `action == "appended"`: collects their OIDs and deletes
     them via `layer.edit_features(deletes=...)`.
  6. For updated rows: JSON-decodes each row's `attachments_added` column
     and deletes those attachment IDs via `attachments.delete_attachments_batch`.
  7. Runs `verify.verify_restore` on the OIDs whose restore reported success
     (best-effort, non-fatal — a verification crash is caught and recorded
     via `verify_error`, distinguished from "nothing to verify" or "verified
     with 0 mismatches").
  8. Re-queries the live feature count (`count_after`); logs a `WARNING`
     (does not raise) if it doesn't match `count_before - <successful
     feature deletes>` — a benign mismatch could mean another process
     touched the layer concurrently.
  9. Clears the ledger entry for every captured feature whose row's `action`
     was `"updated"` or `"appended"`, so a future run reconsiders them.
  10. Logs a one-line console summary and, if `log_path` is given, writes a
      full JSON audit log via `run_log.write_rollback_log` — this write is
      itself best-effort: a failure to write the log is caught and logged,
      never raised, since the rollback (and ledger clear) already completed
      successfully by this point.

  **Raises:** propagates `FileNotFoundError` if `backup_path`/`report_path`
  are missing; raises `LayerMismatchError` per `_check_layer_match` above.
  **Side effects:** network reads/writes (query, `edit_features`
  updates/deletes, attachment deletes), reads backup+report files,
  reads/writes the ledger file, writes the rollback log JSON file (if
  `log_path` given).

## `conflate/verify.py` — post-rollback verification against live AGOL

`rollback.py`'s restore results only reflect whether AGOL *accepted* each
write — not whether the live feature's state now actually matches the
backup. This module closes that gap with a read-only re-check.

- **`_as_dict(feature)`** *(private)* — Normalizes an `arcgis` `Feature`
  object (or anything with an `.as_dict()` method) to a plain
  `{"attributes", "geometry"}` dict.
- **`_fetch_live_by_oid(layer, oids: list[int]) -> dict`** *(private)* —
  Queries `layer` for the given OBJECTIDs, batched at **200 OIDs per
  `OBJECTID IN (...)` query** (`_QUERY_BATCH_SIZE = 200`; this is a
  regression-tested value — a 201-OID input must span exactly 2 queries),
  always at `out_sr=4326` to match how backups were originally captured.
  Returns `{oid: {"attributes": {...}, "geometry": {...}}}`. **Side
  effect:** network query (one or more, per batch).
- **`_lonlat(geometry) -> tuple`**, **`_wkid(geometry)`** *(private)* — Small
  geometry accessors; `_lonlat` returns `(None, None)` for falsy/missing
  geometry, `_wkid` reads `spatialReference.wkid` or
  `spatialReference.latestWkid`.
- **`verify_restore(layer, backup_entries: list[dict], geometry_tolerance_m: float = 0.5) -> list[dict]`**
  — For each backup entry (shaped as written by `backup.write_backup`:
  `{"oid", "attributes", "geometry"}`), re-fetches the live feature and
  diffs it. Returns one result dict per entry:
  `{"oid", "live_feature_found": bool, "verified": bool,
  "attribute_mismatches": {field: (expected, actual), ...},
  "geometry_mismatch_m": float | None}`. `verified` is `True` iff the live
  feature was found, has no attribute mismatches (fields in
  `fields.EXCLUDED_FIELDS` are never compared), and its geometry is within
  `geometry_tolerance_m` meters (default `DEFAULT_GEOMETRY_TOLERANCE_M =
  0.5`) of the backup's — a nonzero default because a restore round-trips
  through reprojection and isn't expected to be bit-exact. If the backup and
  live geometries report **different spatial-reference WKIDs**, that's
  flagged as a mismatch directly without computing a geodesic distance
  (comparing raw x/y across two different SRs would produce a meaningless
  number). Returns `[]` immediately if `backup_entries` is empty (no query
  made). **Side effect:** network query only (strictly read-only).

## `conflate/run_log.py` — durable rollback audit log

- **`write_rollback_log(path, *, layer, authoritative_url, backup_path, report_path, authoritative_feature_count_before, authoritative_feature_count_after, expected_feature_count_after, restore_results, feature_delete_results, attachment_delete_results, total_attachments_targeted, total_attachments_removed, restore_success_count, restore_failure_count, delete_feature_success_count, delete_feature_failure_count, ledger_cleared_count, verify_results, verify_success_count, verify_mismatch_count, verify_error) -> None`**
  — Writes one comprehensive JSON record of a rollback run to `path` (see
  [Artifact schemas](#artifact-schemas) for the exact payload shape,
  including the derived `feature_count_matches_expected` boolean). This is a
  **write-only audit artifact** — nothing else in the codebase reads it
  back. **Side effect:** file write; creates parent directories.

---

## Artifact schemas

These are contracts between modules that no single function signature makes
visible on its own — several are load-bearing (changing a column name here
silently breaks something else in the pipeline).

### Dry-run report CSV

Columns: `captured_global_id`, `action` (one of `would_update` /
`would_append` / `skipped_no_geometry`), `matched_authoritative_oid`,
`distance_m`, `threshold_m`, `layer`.

### Apply report CSV

**Different column set from the dry-run report.** Columns:
`captured_global_id`, `action` (`updated` or `appended`),
`authoritative_oid`, `distance_m` (`None` for appends), `threshold_m`,
`success`, `error`, `attachments_status` (a `"copied/total"` string, `"0/0"`,
or empty if attachments weren't enabled), `attachments_added` (a
**JSON-encoded list** of newly-added attachment IDs — encoded as a JSON
string, not a raw Python list, because `csv.DictWriter` would otherwise
stringify a list in a way that can't be parsed back), `ledgered` (bool),
`layer`.

**`rollback.py` depends on this exact schema**: it filters rows on
`action == "updated"` / `action == "appended"`, reads `authoritative_oid` to
know which live feature to restore/delete, and `json.loads()`s
`attachments_added` to know which attachment IDs to remove. Renaming or
repurposing any of these columns breaks rollback for reports already
written with the old schema, and will break it going forward if
`rollback.py` isn't updated to match.

### Ledger entry (`state/<layer>.json`, keyed by `captured_global_id`)

```json
{
  "action": "updated" | "created",
  "authoritative_oid": 123,
  "attachments_status": "2/3",
  "run_time": "2026-07-27T10:00:00"
}
```

Note the ledger's `action` value for an appended feature is **`"created"`**,
not `"appended"` — it uses different vocabulary than the report CSV's
`action` column for the exact same event (`cli.py`'s update loop passes
`ledger_action="updated"`, its append loop passes `ledger_action="created"`,
while the report rows for the same two loops use `"updated"`/`"appended"`).
Don't assume the two `action` fields share a vocabulary when reading or
writing code that touches both.

### Backup JSON (`backups/<layer>_<timestamp>.json`)

```json
{
  "layer": "hydrants",
  "authoritative_url": "https://services.arcgis.com/.../FeatureServer/8",
  "entries": [
    {"oid": 123, "attributes": {...}, "geometry": {...}}
  ]
}
```

Legacy backups written before layer identity was tracked are a bare JSON
list (`[{"oid": ..., "attributes": ..., "geometry": ...}, ...]`) with no
`layer`/`authoritative_url` wrapper; `backup.load_backup_meta` normalizes
these to the shape above with `layer`/`authoritative_url` set to `None`.

### Rollback audit log JSON (`reports/<layer>_<ts>_rollback_<ts>.json`)

Written by `run_log.write_rollback_log`. Top-level fields: `layer`,
`authoritative_url`, `backup_path`, `report_path`,
`authoritative_feature_count_before`, `authoritative_feature_count_after`,
`expected_feature_count_after`, `feature_count_matches_expected` (derived
bool), `restore_results`, `feature_delete_results`,
`attachment_delete_results`, `total_attachments_targeted`,
`total_attachments_removed`, `restore_success_count`,
`restore_failure_count`, `delete_feature_success_count`,
`delete_feature_failure_count`, `ledger_cleared_count`, `verify_results`,
`verify_success_count`, `verify_mismatch_count`, `verify_error`. Nothing in
the tool reads this file back — it exists purely as a durable record for a
human to inspect after the fact.

---

## Invariants

Properties that hold across the whole pipeline but aren't visible from any
single function signature:

- **Everything is WGS84.** `paging.fetch_all_features` forces
  `out_sr=4326` on every query, and `verify._fetch_live_by_oid` does the
  same for its re-check — so every `lon`/`lat` anywhere in the pipeline is
  already in WGS84 degrees, regardless of either layer's native spatial
  reference. This is why `geometry.reproject_point` exists but is currently
  **unwired** (not called from `cli.py`, `rollback.py`, or `verify.py`) —
  it's a standalone utility, not part of the active pipeline.
- **Point geometry only.** `gis_client.validate_geometry_type` enforces this
  at startup for both layers; any non-point/missing-geometry feature
  encountered later is skipped via `cli._has_point_geometry` rather than
  crashing the run.
- **One-to-one match claiming persists across runs**, not just within one —
  via `cli._seed_claimed_oids` reading every ledger entry's
  `authoritative_oid` at the start of each run.
- **Ledgering requires full success.** A captured feature is only marked
  processed (and thus skipped on future runs) if both its write **and** its
  attachment copy (when enabled) fully succeeded. A partial failure leaves
  it unledgered, so it's retried automatically next run.
- **Backups happen before any write**, for every planned update, in the
  same apply run — so even if the apply step fails partway through, the
  backup already on disk reflects true pre-edit state for everything that
  was *about* to be written.

---

## Config schema reference

### `config.json` — per-layer keys

| Key | Type | Required | Notes |
|---|---|---|---|
| `authoritative_url` | string | Yes | REST URL of the authoritative FeatureLayer. |
| `captured_url` | string | Yes | REST URL of the captured FeatureLayer. |
| `match_threshold_m` | number | Yes | Max geodesic distance (meters) for a match. |
| `field_map` | object | Yes (may be `{}`) | `{captured_field: authoritative_field}` for renamed fields. |
| `copy_attachments` | bool | Yes | Requires the authoritative layer to have attachments enabled if `true`. |
| `type_field_authoritative` | string | No | Must be paired with `type_field_captured`. |
| `type_field_captured` | string | No | Must be paired with `type_field_authoritative`. |

Enforced by `config.validate_layer_config`.

### `config.local.json`

| Key | Type | Required | Notes |
|---|---|---|---|
| `portal_url` | string | Yes | AGOL/Portal base URL. |
| `auth_type` | string | No (default `"builtin"`) | `"builtin"` or `"oauth"`. |
| `username` | string | With `auth_type: builtin` | |
| `password` | string | With `auth_type: builtin` | |
| `client_id` | string | With `auth_type: oauth` | Registered AGOL application's client ID. |
| `profile` | string | With `auth_type: oauth` | Local profile name for cached token storage. |

Read by `gis_client.connect`; see `config.local.json.example` and
`config.local.oauth.json.example` for filled-in templates.

---

## Test-to-module map

| Test file | Module(s) covered |
|---|---|
| `test_gis_client.py` | `gis_client.py` (`validate_geometry_type`) |
| `test_attachments.py` | `attachments.py` |
| `test_cli.py` | `cli.py`'s pure helpers (`_simplify_feature`, `_has_point_geometry`, `_attachments_fully_succeeded`, `_seed_claimed_oids`) |
| `test_cli_main.py` | `cli.py`'s `main()` end-to-end, via a fake feature layer (no real network) |
| `test_rollback.py` | `rollback.py` (`rollback`, `LayerMismatchError`) |
| `test_run_log.py` | `run_log.py` (`write_rollback_log`) |
| `test_verify.py` | `verify.py` (`verify_restore`) |
| `test_backup.py` | `backup.py` (`write_backup`, `load_backup`, `load_backup_meta`) |
| `test_ledger.py` | `ledger.py` (`load_ledger`, `save_ledger`, `mark_processed`, `is_processed`) |
| `test_matching.py` | `matching.py` (`pick_closest`, `pick_closest_unclaimed`, `find_candidates`), plus `geometry.geodesic_distance` |
| `test_nullfill.py` | `nullfill.py` (`is_null`, `build_field_updates`) |
| `test_threshold.py` | `threshold.py` (`format_threshold_both_units`) |

`config.py`, `paging.py`, `apply.py`, `report.py`, and `fields.py` have no
dedicated test file of their own; their behavior is exercised indirectly
through `test_cli_main.py`'s end-to-end run.

---

## "Where to look" quick index

Common modification scenarios and the file(s) to touch:

| Want to... | Touch |
|---|---|
| Change matching/distance logic | `conflate/matching.py`, `conflate/geometry.py` + `tests/test_matching.py` |
| Change what counts as "null" for null-fill | `conflate/nullfill.py` + `tests/test_nullfill.py` |
| Add/change a system field that must never be written | `conflate/fields.py` (used by `cli.py`, `rollback.py`, `verify.py`) |
| Change attachment naming/copy/delete behavior | `conflate/attachments.py` + `tests/test_attachments.py` |
| Add a new CLI flag or change argument parsing | `conflate/cli.py`'s `_build_arg_parser` + `tests/test_cli.py` |
| Change the apply/dry-run report's columns | `conflate/cli.py`'s `main()`/`_build_outcome_row` — remember `rollback.py` depends on the exact column names (see [Artifact schemas](#artifact-schemas)) |
| Add a new rollback safety check | `conflate/rollback.py`'s `_check_layer_match` (or a new guard alongside it) + `tests/test_rollback.py` |
| Change post-restore verification tolerance/logic | `conflate/verify.py` + `tests/test_verify.py` |
| Change what's recorded in the rollback audit log | `conflate/run_log.py` (schema) and `conflate/rollback.py` (call site) + `tests/test_run_log.py` |
| Change AGOL auth handling | `conflate/gis_client.py`'s `connect` + `tests/test_gis_client.py` |
| Change ledger persistence/shape | `conflate/ledger.py` + `tests/test_ledger.py` |
