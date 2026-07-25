# Code Review — Phase 8 "Apply Changes" implementation

**Date:** 2026-07-20
**Scope:** `data-conflation/conflate.py` (checkpoint/resume + batch update/append to AGOL + attachment migration) and `data-conflation/config.json`, diffed against `origin/main` including uncommitted working-tree changes.
**Not in scope:** `plan-phase10.md` (Restore Functionality) — not yet implemented.
**Method:** 8 parallel finder passes (line-by-line diff scan, removed-behavior/spec audit, reuse, simplification, efficiency, altitude, conventions) cross-checked against `plan-phase8.md`, deduplicated, and verified against the source directly.

## Critical correctness bugs

### 1. `--resume` can never find a previous checkpoint (CONFIRMED)
`resolve_paths()` (`conflate.py:258`) bakes a fresh `datetime.now()` timestamp into `checkpoint_file` on **every** invocation of the script. Since this runs once per process at the top of `main()`, a `--resume` run computes a *different* checkpoint filename than the interrupted run wrote.

**Effect:** run `--apply`, it crashes partway through and writes `backup/Layer_checkpoint_20260101_120000.json`. Re-run `--apply --resume` a minute later — a new timestamp is computed, `manage_checkpoint()` looks for a file that doesn't exist, and the script exits with `"No checkpoint found... Cannot resume."` The entire crash-recovery feature — the headline deliverable of Phase 8 — does not work.

**Fix direction:** checkpoint discovery needs to glob for an existing `{layer}_checkpoint_*.json` (the same pattern Phase 10's planned `list_backups()` already uses for backup files) rather than deriving the path from the current run's timestamp.

### 2. Batch API response is never checked for partial failures (CONFIRMED)
`apply_updates_in_batches`/`apply_appends_in_batches` (`conflate.py:1668`, `1773`) call `auth_layer.edit_features(...)` and only treat a **raised exception** as failure. AGOL's `edit_features` normally returns HTTP 200 with per-feature `success`/`error` entries in `updateResults`/`addResults` — it does not raise for individual record failures. The `result` variable is captured but never inspected.

**Effect:** a batch of 50 updates where 1 record fails a field/domain validation still returns success (no exception); all 50 GlobalIDs — including the failed one — get written to the checkpoint and reported as applied. That record's real update never happened in AGOL and will never be retried, because the checkpoint says it's done.

### 3. Update payloads never carry geometry (CONFIRMED)
`build_update_payload` (`conflate.py:1446`) only returns an attribute dict; `apply_updates_in_batches` (`conflate.py:1657`) sends this flat dict (plus a bolted-on lowercase `"globalid"` key) directly as the update, instead of the `{"attributes": {...}, "geometry": {...}}` shape correctly used for appends (`conflate.py:1763`).

**Effect:** for every "clean"/"ambiguous" matched record, attributes are updated but the captured point geometry is never written back — the tool's core purpose (conflating captured positions into the authoritative layer) silently fails for every update. `plan-phase8.md` explicitly requires geometry + attributes in a single call.

### 4. New-record checkpoint dedup is inert on the common path (CONFIRMED)
`record["_new_globalid"]` (`conflate.py:1757`) is only ever set when `use_global_ids` is true, and is never derived from AGOL's actual response. When `use_global_ids` is `False` (the default from `auth_info.get("use_global_ids", False)`, common for layers that don't accept client-supplied GlobalIDs), `checkpoint["applied_new"]` is never populated.

**Effect:** 100 new records in 2 batches; batch 1 (50 records) succeeds and appends to AGOL, then the process is killed before batch 2. On `--apply --resume`, the dedup filter (`_new_globalid not in checkpoint["applied_new"]`) is true for all 100 records again, so the 50 already-appended records get appended a **second time** — duplicate features created in the authoritative layer.

### 5. Notes field is appended to, not rebuilt fresh (CONFIRMED)
`build_update_payload` (`conflate.py:1481`) does `combined = f"{existing_notes} | {new_notes}"`, directly contradicting the explicit instruction in `plan-phase8.md`: *"Rebuild the notes field fresh (do NOT append to existing notes)."*

**Effect:** any record touched across multiple runs (resume, or a re-run after partial failure) accumulates duplicate note text each pass, eventually exceeding `notes_max_length` and silently truncating away the newest content.

## Edge cases (PLAUSIBLE)

### 6. `pd.NA` not recognized as "missing" in notes handling
`conflate.py:1482` — the existing-notes null check only special-cases the literal string `"nan"`, not pandas' `pd.NA` sentinel. If `COMMENTNOTES` uses a nullable pandas dtype, a null value stringifies to `"<NA>"` and that literal text gets written into AGOL.

### 7. `_to_native()` is dead code
`conflate.py:1420` — this numpy/pandas → native-Python coercion helper is never called. `build_update_payload`/`build_append_payload` pass raw `numpy.int64`/`float64`/`Timestamp` values straight into the AGOL payload, risking serialization failures that `_is_retryable_error`'s keyword heuristic could misclassify as retryable (wasting 3 retries with backoff before ultimately failing).

### 8. Checkpoint control fields leak into persisted state
`conflate.py:2120` — `checkpoint["_path"]` and `checkpoint["max_retries"]` are runtime-only values stored directly on the checkpoint dict and re-serialized into the on-disk JSON on every save. If `_path` is ever missing when `save_checkpoint(checkpoint.get("_path", ""), checkpoint)` runs, it silently writes to the current working directory instead of raising — a crash at that point loses progress with no visible error.

## Cleanup

### 9. Per-record `save_checkpoint()` calls + duplicated fallback logic
`conflate.py:1697/1707/1802/1817` — the one-at-a-time fallback loops in both apply functions call `save_checkpoint()` (a full `os.makedirs` + `json.dump`) after **every single record** instead of once per fallback loop. The entire batch → fallback → checkpoint-save sequence is also duplicated near-verbatim between `apply_updates_in_batches` and `apply_appends_in_batches`, so a fix to the sequencing (e.g. #8 above) has to be applied in four places.

### 10. `checkpoint_add_update`/`checkpoint_add_new` are dead code
`conflate.py:1326` — fully implemented, documented helpers with zero call sites. The actual apply functions reimplement the same load-modify-save pattern inline via direct dict mutation + `save_checkpoint()`, bypassing these helpers. A future maintainer fixing a checkpoint bug in the "obvious" helper won't touch the real code path.

## Other findings surfaced but not in the top 10 (lower severity / for awareness)

- Per-record logging (`"Updated OBJECTID <oid> ... — <field_count> fields changed"`) only fires on the one-at-a-time fallback path, not the batch-success happy path — the audit trail spec requires is largely absent on the common case.
- Truncation warnings drop the required `OBJECTID <oid>` suffix, making truncations untraceable to a specific record.
- `O(n)` pandas boolean-mask lookup (`auth_wgs84[auth_wgs84["GlobalID"] == auth_gid]`) runs once per record inside the update loops instead of building a `GlobalID → row` dict once — O(N×M) on large layers.
- `auth_layer.attachments.get_list(auth_gid)` (attachment migration) is called once per *attachment* instead of once per *record*, tripling AGOL calls when records average 3 attachments.
- `_is_retryable_error` classifies purely via `str(e).lower()` keyword matching rather than exception types/HTTP status — fragile to wording changes in the arcgis SDK or server.
- The notes-concatenation block is duplicated near-identically across `build_update_payload`, `build_append_payload`, and a third variant in `build_proposed_new_gdf`.
- Dead import: `from arcgis.features import FeatureLayer as FL` in `apply_updates_in_batches` — `FL` is never referenced.

## Priority recommendation

Findings **#1–#5 block Phase 8 from being safe to run against production AGOL data** — #3 means updates silently never move geometry, #2 and #4 mean the checkpoint can lie about what was actually applied, and #1 means the resume feature (the reason checkpoints exist at all) doesn't function. These should be fixed and re-tested (per `manual_testing.md` §8) before `--apply` is used against a real authoritative layer, and before building Phase 10 (Restore) on top of this apply flow.
