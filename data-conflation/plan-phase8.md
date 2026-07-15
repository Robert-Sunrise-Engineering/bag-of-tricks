# Phase 8: Apply Changes

## Objective
Write matched and new records to the authoritative layer in AGOL, with checkpoint/resume support for crash recovery.

## Dependencies
- Phase 7 must be complete: dry run output working
- Requires authenticated AGOL connection and layer info from Phase 2

## Deliverables

### 1. Checkpoint Resume

Before applying any changes:
1. Check if a checkpoint file exists at `backup/{layer}_checkpoint_{timestamp}.json`
2. If it exists:
   - Print: `"Previous checkpoint found. Resume from previous run? [Y/n]"`
   - If user answers "y" (or just presses Enter):
     - Load the checkpoint file
     - Skip any records whose GlobalID is already in `applied_updates` or `applied_new`
     - Print: `"Resuming: <n> updates and <n> new records already applied"`
   - If user answers "n":
     - Print: `"Starting fresh. Ignoring previous checkpoint."`
     - Delete the old checkpoint file
     - Create a new checkpoint file with empty lists
3. If no checkpoint exists:
   - Create a new checkpoint file at `backup/{layer}_checkpoint_{current_timestamp}.json`
   - Initialize with empty `applied_updates` and `applied_new` lists

### 2. Update Matched Records

For each matched record (match_type is "clean" or "ambiguous"):
1. Skip if the record's GlobalID is already in the checkpoint's `applied_updates`
2. Build the update payload:
   - **Geometry**: set to the captured geometry (WGS 84)
   - **Attributes**: for each field that exists in the authoritative layer schema:
     - If the captured value is non-null: use the captured value
     - If the captured value is null: keep the existing authoritative value (do NOT set to null)
   - **Notes field**:
     - For fields that exist in auth schema but are null in captured: concatenate into notes
     - Format: `"FieldName: value | FieldName: value"`
     - Rebuild the notes field fresh (do NOT append to existing notes)
     - Truncate to `notes_max_length` if needed; log warning: `"Notes truncated to <max> chars for OBJECTID <oid>"`
3. Use `arcgis.features.FeatureLayer.edit_features()` to update the record:
   - Pass the GlobalID to identify the record
   - Update geometry and attributes in a single call
4. On success:
   - Add the GlobalID to the checkpoint's `applied_updates`
   - Save the checkpoint file
   - Log: `"Updated OBJECTID <oid> (GlobalID <gid>) — <field_count> fields changed"`
5. On failure:
   - Log error: `"Failed to update OBJECTID <oid> (GlobalID <gid>): <error>"`
   - Do NOT add to checkpoint (will be retried on resume)
   - Continue to next record (do NOT abort the entire operation)

### 3. Append New Records

For each unmatched record (match_type is "new"):
1. Skip if the record's GlobalID is already in the checkpoint's `applied_new`
2. Build the append payload:
   - **Attributes**: for each field that exists in the authoritative layer schema:
     - If the captured value is non-null: use the captured value
     - If the captured value is null: skip this field (don't include it in the append)
   - **Notes field**:
     - Concatenate non-matching captured attributes into notes
     - Rebuild fresh (not append to existing)
     - Truncate to `notes_max_length` if needed; log warning
   - **Geometry**: the captured geometry (WGS 84)
3. Use `arcgis.features.FeatureLayer.edit_features()` with the `appends` parameter:
   - Pass the attribute dict and geometry
4. On success:
   - Add the new record's GlobalID to the checkpoint's `applied_new`
   - Save the checkpoint file
   - Log: `"Appended new record — captured OBJECTID <oid>"`
5. On failure:
   - Log error: `"Failed to append new record — captured OBJECTID <oid>: <error>"`
   - Do NOT add to checkpoint (will be retried on resume)
   - Continue to next record

### 4. Batch Size

- Process matched records in batches of up to 50 records per `edit_features()` call
- Process new records in batches of up to 50 records per `edit_features()` call
- If a batch fails, fall back to one-at-a-time processing for that batch
- Log batch progress: `"Updated batch <n>/<total>"`

### 5. Cleanup

After all records are processed:
1. If all records were successfully applied (no failures):
   - Delete the checkpoint file
   - Print: `"Checkpoint deleted — all changes applied successfully"`
2. If any records failed:
   - Preserve the checkpoint file
   - Print: `"Checkpoint preserved at <path> — <n> failures remain. Re-run to resume."`

### 6. Integration in Main Flow

In `conflate.py`, when `--apply` flag is set:
1. Initialize checkpoint (resume or new)
2. Update matched records (with checkpointing after each)
3. Append new records (with checkpointing after each)
4. Cleanup checkpoint
5. Print final summary

## Error Handling

| Error | Action |
|-------|--------|
| AGOL timeout | Retry with exponential backoff (1s, 2s, 4s, 8s, max 3 retries) |
| Network error | Retry with exponential backoff |
| Invalid field value | Log error, skip record, continue |
| Geoprocessing error | Log error, skip record, continue |
| Checkpoint write failure | Log error, attempt to continue, checkpoint may be lost |

## Test Criteria
- `--apply` mode updates matched records in AGOL with correct geometry and attributes
- `--apply` mode appends new records to AGOL with correct attributes
- Checkpoint file is created and updated after each successful operation
- Resume correctly skips already-applied records
- Failed records are not added to checkpoint and are retried on resume
- Checkpoint is deleted on full success
- Checkpoint is preserved on any failure
- Batch processing works for large datasets (50 records per batch)
- Notes field truncation works correctly and logs warnings
- Null captured values do not overwrite existing authoritative values
