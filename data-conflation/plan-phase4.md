# Phase 4: Schema Validation & Backup

**Status: COMPLETE** ✅

## Objective
Validate the authoritative layer schema before processing and create a recoverable backup of the current authoritative data.

## Dependencies
- Phase 3 must be complete: data loading and CRS handling working

## Deliverables

### 1. Schema Validation ✅

Implemented `validate_schema(gdf_auth, layer_info)` in `conflate.py:491`:
1. Checks that the `COMMENTNOTES` field exists in the authoritative GeoDataFrame's columns
2. If the `COMMENTNOTES` field does NOT exist:
   - Prints error: `"FATAL: Authoritative layer '<name>' is missing the required 'COMMENTNOTES' field. Aborting."`
   - Exits with code 1
3. If the `COMMENTNOTES` field exists, determines its constraints:
   - For text fields: records the `length` property (max character length)
   - For non-text fields: records the field type and notes that truncation may apply
   - Stores as `notes_max_length` in the returned result
4. Returns a dict with:
   - `valid` — boolean (always True if no exception raised)
   - `notes_max_length` — integer max length, or `None` if not applicable
   - `field_types` — dict mapping field names to their types (for reference)

**Note:** Field name changed from `notes` to `COMMENTNOTES` to match the actual AGOL layer schema.

### 2. Backup Creation ✅

Implemented `create_backup(gdf_auth, backup_path, layer_name)` in `conflate.py:553`:
1. Takes the authoritative GeoDataFrame in WGS 84
2. Ensures the `backup/` directory exists (creates if not)
3. Exports the GeoDataFrame to a GeoPackage file at `backup_path`
4. Preserves all fields including `OBJECTID` and `GlobalID`
5. Uses `to_file()` with driver `"GPKG"` from geopandas
6. On success, prints: `"Backup created: <backup_path>"`
7. On failure:
   - Prints error: `"FATAL: Backup failed: <error>"`
   - Exits with code 1 (backup is mandatory; conflation must not proceed without it)

### 3. Checkpoint File Structure ✅

Implemented checkpoint file management for later use (Phase 8). The checkpoint file is a JSON file at `backup/{layer}_checkpoint_{timestamp}.json` with this structure:

```json
{
  "timestamp": "20260714_143022",
  "layer": "LayerName",
  "applied_updates": ["<GlobalID_1>", "<GlobalID_2>", ...],
  "applied_new": ["<GlobalID_3>", ...]
}
```

Implemented helper functions in `conflate.py:565-640`:
- `load_checkpoint(checkpoint_path)` — loads and returns the checkpoint dict, or returns `None` if file doesn't exist
- `save_checkpoint(checkpoint_path, checkpoint_data)` — writes checkpoint data to JSON
- `checkpoint_add_update(checkpoint_path, global_id)` — appends a GlobalID to `applied_updates`
- `checkpoint_add_new(checkpoint_path, global_id)` — appends a GlobalID to `applied_new`

### 4. Integration in Main Flow ✅

In `conflate.py:643`, after data loading (Phase 3), before matching (Phase 5):
1. Calls `validate_schema(gdf_auth, layer_info)` — aborts if invalid
2. Calls `create_backup(gdf_auth, backup_file, layer_name)` — aborts if failed
3. Both steps must complete successfully before proceeding

## Edge Cases

| Case | Handling |
|------|----------|
| `COMMENTNOTES` field missing | Abort with clear error message |
| `COMMENTNOTES` field is non-text type | Record type, proceed with validation |
| Backup directory doesn't exist | Create it automatically |
| Backup write fails (permissions, disk space) | Abort with clear error |
| GeoDataFrame has no geometry column | This should not happen (caught in Phase 3), but log error if it does |

## Test Criteria

| Criterion | Status |
|-----------|--------|
| `validate_schema()` passes when `COMMENTNOTES` field exists, aborts when it doesn't | ✅ PASS |
| `validate_schema()` correctly records `COMMENTNOTES` field max length for text fields | ✅ PASS |
| `validate_schema()` handles AGOL field type format (`esriFieldTypeString`) | ✅ PASS |
| `create_backup()` produces a valid GeoPackage file with all fields preserved | ✅ PASS |
| `create_backup()` creates the backup directory if it doesn't exist | ✅ PASS |
| `create_backup()` aborts on write failure | ✅ PASS |
| Checkpoint file I/O functions correctly read/write/modify JSON checkpoint files | ✅ PASS |
| Empty GeoDataFrames can be backed up (valid GPKG with no rows) | ✅ PASS |

## Unit Tests
- 74/74 tests pass in `tests/test_conflate.py`
- Test classes: `TestValidateSchema` (5 tests), `TestCreateBackup` (5 tests), `TestCheckpoint` (8 tests)

## Manual Tests
- 21/21 manual tests pass against live AGOL layer `Water_Hydrants`
- Tests cover: CLI flags, data loading, CRS handling, schema validation, backup creation, checkpoint I/O, timestamp uniqueness

## Live System Results
- Layer: `Water_Hydrants` (189 features)
- COMMENTNOTES field: String, length=255
- UTM zone detected: EPSG:32612 (UTM 12N)
- Backup files created successfully with all fields preserved
