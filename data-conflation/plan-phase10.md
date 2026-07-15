# Phase 10: Restore Functionality

## Objective
Restore the authoritative layer to a previous state using a timestamped backup GeoPackage, providing a recovery path for failed or unsatisfactory conflations.

## Dependencies
- Phase 4 must be complete: backup files exist in GeoPackage format
- Phase 2 must be complete: AGOL authentication and layer info retrieval working

## Deliverables

### 1. List Available Backups

Implement a `list_backups(layer_name, backup_dir)` function that:
1. Scans the `backup/` directory for files matching the pattern `{layer_name}_backup_*.gpkg`
2. Extracts the timestamp from each filename
3. Returns a list of dicts sorted by timestamp (oldest first):

```python
[
    {"filename": "LayerName_backup_20260714_100000.gpkg", "timestamp": "20260714_100000", "path": "backup/LayerName_backup_20260714_100000.gpkg"},
    {"filename": "LayerName_backup_20260714_143022.gpkg", "timestamp": "20260714_143022", "path": "backup/LayerName_backup_20260714_143022.gpkg"}
]
```

4. If no backups are found:
   - Print: `"No backups found for layer '<layer_name>' in <backup_dir>"`
   - Exit with code 1

### 2. User Selection

If the `--restore` flag is set:
1. Print the list of available backups with numbered options:
   ```
   Available backups for '<layer_name>':
     1. 20260714_100000
     2. 20260714_143022
   Enter backup number to restore from (or 0 to cancel):
   ```
2. Read user input
3. If user enters 0 or an invalid option:
   - Print: `"Restore cancelled."`
   - Exit cleanly (code 0)
4. If user enters a valid number:
   - Select the corresponding backup file path
   - Proceed with restoration

### 3. Validation Before Restore

Before restoring:
1. Verify the selected backup file exists
2. Check that the GeoPackage is readable (try loading it with geopandas)
3. Verify that the authoritative layer supports client-supplied GlobalIDs:
   - Check `layer_info.use_global_ids` — if False, print:
     `"FATAL: Layer '<name>' does not support client-supplied GlobalIDs. Restore not possible."`
   - Exit with code 1
4. Print a warning:
   ```
   WARNING: Restoring from backup '<timestamp>' will REPLACE all current data in the authoritative layer.
   This action cannot be undone.
   ```
5. Prompt for confirmation:
   ```
   Type 'RESTORE' to confirm:
   ```
6. If user does not type exactly `RESTORE`:
   - Print: `"Restore cancelled."`
   - Exit cleanly (code 0)

### 4. Restore Operation

Perform the restore:
1. Load the backup GeoPackage into a GeoDataFrame:
   ```python
   gdf = geopandas.read_file(backup_path)
   ```
2. Verify the GeoDataFrame has the expected fields (at minimum OBJECTID and GlobalID)
3. Use the ArcGIS REST API to replace the layer content:
   - Option A: Use `FeatureLayerCollection.replace()` if available
   - Option B: Delete all existing records and append the backup records:
     - Delete all: `edit_features(deletes=[{"where": "1=1"}])`
     - Append all: `edit_features(appends=[...])`
   - Option C: Use `FeatureLayerCollection.overwrite()` if available
4. On success:
   - Print: `"Restore complete — layer '<name>' restored to state from <timestamp>"`
5. On failure:
   - Print: `"FATAL: Restore failed: <error>"`
   - Exit with code 1

### 5. Integration in Main Flow

In `conflate.py`, when `--restore` flag is set:
1. Skip all other processing (no data loading, matching, etc.)
2. Call `list_backups(layer_name, backup_dir)`
3. Prompt user for selection
4. Validate before restore
5. Perform restore operation
6. Print completion message

## Edge Cases

| Case | Handling |
|------|----------|
| No backups exist | List function exits with code 1 |
| Backup file was deleted manually | File existence check catches this |
| Backup GeoPackage is corrupted | GeoDataFrame read fails, caught and reported |
| Layer doesn't support GlobalIDs | Validated before restore, exits with code 1 |
| User cancels restore | Clean exit, no changes made |
| Restore fails mid-way | Layer may be in partial state; user must re-attempt |

## Test Criteria
- `list_backups()` finds and lists all backup files for a layer
- `list_backups()` exits with code 1 when no backups exist
- User selection works for valid and invalid inputs
- Cancel (0 or wrong text) exits cleanly without changes
- Confirmation prompt requires exact "RESTORE" text
- Layers without GlobalID support are rejected
- Restore correctly replaces layer content with backup data
- Restored data matches the backup file (same records, same attributes)
