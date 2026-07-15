# Phase 4: Schema Validation & Backup

## Objective
Validate the authoritative layer schema before processing and create a recoverable backup of the current authoritative data.

## Dependencies
- Phase 3 must be complete: data loading and CRS handling working

## Deliverables

### 1. Schema Validation

Implement a `validate_schema(gdf_auth, layer_info)` function that:
1. Checks that a `notes` field exists in the authoritative GeoDataFrame's columns
2. If the `notes` field does NOT exist:
   - Print error: `"FATAL: Authoritative layer '<name>' is missing the required 'notes' field. Aborting."`
   - Exit with code 1
3. If the `notes` field exists, determine its constraints:
   - For text fields: record the `length` property (max character length)
   - For non-text fields: record the field type and note that truncation may apply
   - Store as `notes_max_length` in the returned result
4. Returns a dict with:
   - `valid` — boolean (always True if no exception raised)
   - `notes_max_length` — integer max length, or `None` if not applicable
   - `field_types` — dict mapping field names to their types (for reference)

### 2. Backup Creation

Implement a `create_backup(gdf_auth, backup_path, layer_name)` function that:
1. Takes the authoritative GeoDataFrame in WGS 84
2. Ensures the `backup/` directory exists (create if not)
3. Exports the GeoDataFrame to a GeoPackage file at `backup_path`
4. Preserves all fields including `OBJECTID` and `GlobalID`
5. Uses `to_file()` with driver `"GPKG"` from geopandas
6. On success, print: `"Backup created: <backup_path>"`
7. On failure:
   - Print error: `"FATAL: Backup failed: <error>"`
   - Exit with code 1 (backup is mandatory; conflation must not proceed without it)

### 3. Checkpoint File Structure

Implement checkpoint file management for later use (Phase 8). The checkpoint file is a JSON file at `backup/{layer}_checkpoint_{timestamp}.json` with this structure:

```json
{
  "timestamp": "20260714_143022",
  "layer": "LayerName",
  "applied_updates": ["<GlobalID_1>", "<GlobalID_2>", ...],
  "applied_new": ["<GlobalID_3>", ...]
}
```

Implement helper functions:
- `load_checkpoint(checkpoint_path)` — loads and returns the checkpoint dict, or returns `None` if file doesn't exist
- `save_checkpoint(checkpoint_path, checkpoint_data)` — writes checkpoint data to JSON
- `checkpoint_add_update(checkpoint_path, global_id)` — appends a GlobalID to `applied_updates`
- `checkpoint_add_new(checkpoint_path, global_id)` — appends a GlobalID to `applied_new`

### 4. Integration in Main Flow

In `conflate.py`, after data loading (Phase 3), before matching (Phase 5):
1. Call `validate_schema(gdf_auth, layer_info)` — abort if invalid
2. Call `create_backup(gdf_auth, backup_file, layer_name)` — abort if failed
3. Both steps must complete successfully before proceeding

## Edge Cases

| Case | Handling |
|------|----------|
| `notes` field missing | Abort with clear error message |
| `notes` field is non-text type | Record type, proceed with validation |
| Backup directory doesn't exist | Create it automatically |
| Backup write fails (permissions, disk space) | Abort with clear error |
| GeoDataFrame has no geometry column | This should not happen (caught in Phase 3), but log error if it does |

## Test Criteria
- `validate_schema()` passes when `notes` field exists, aborts when it doesn't
- `validate_schema()` correctly records `notes` field max length for text fields
- `create_backup()` produces a valid GeoPackage file with all fields preserved
- `create_backup()` creates the backup directory if it doesn't exist
- `create_backup()` aborts on write failure
- Checkpoint file I/O functions correctly read/write/modify JSON checkpoint files
- Empty GeoDataFrames can be backed up (valid GPKG with no rows)
