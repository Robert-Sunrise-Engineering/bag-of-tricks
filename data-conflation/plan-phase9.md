# Phase 9: Attachment Migration

## Objective
Migrate attachments from captured records to their matched authoritative records in AGOL. This is an optional step enabled by the `--migrate-attachments` flag.

## Dependencies
- Phase 8 must be complete: apply changes working
- Requires match results from Phase 6 (only matched records have attachments to migrate)
- Requires AGOL authenticated connection and layer info

## Deliverables

### 1. Attachment Migration Logic

For each matched record (match_type is "clean" or "ambiguous", NOT "new"):
1. Query attachments from the **captured** record using its OBJECTID
2. For each attachment found:
   - Download the attachment data (binary content)
   - Check if an attachment with the same name already exists on the **authoritative** record (by GlobalID)
   - If it already exists: skip and log `"Attachment '<name>' already exists on GlobalID <gid>, skipping"`
   - If it does not exist: add the attachment to the authoritative record using its GlobalID
   - Log: `"Migrated attachment '<name>' (<size> bytes, <type>) from OBJECTID <captured_oid> to GlobalID <auth_gid>"`
3. Do NOT delete attachments from the captured record

### 2. Attachment Query API

Use the ArcGIS REST API for attachments:
- Query attachments: `https://<server>/arcgis/rest/services/<path>/FeatureServer/<layer_id>/<objectid>/attachments`
- Download attachment: `https://<server>/arcgis/rest/services/<path>/FeatureServer/<layer_id>/<objectid>/attachments/<attid>/f?p=<attid>`
- Add attachment: POST to `https://<server>/arcgis/rest/services/<path>/FeatureServer/<layer_id>/<auth_globalid>/attachments/add`

### 3. Attachment Metadata

For each attachment, capture and store:
- `name` — attachment file name
- `size_bytes` — size in bytes
- `type` — MIME type (e.g., "image/jpeg", "application/pdf")
- `status` — "migrated" on success, "skipped" if already exists, "failed" on error

### 4. Independent Failures

Attachment migration is independent per attachment:
- If one attachment fails to migrate, log the error and continue to the next
- Do NOT abort the entire migration
- Track failed attachments in a summary list
- Log at the end: `"Attachment migration complete: <migrated> migrated, <skipped> skipped, <failed> failed"`

### 5. Dry Run Behavior

In dry run mode (`--migrate-attachments` without `--apply`):
- Query attachments from captured records
- Do NOT download or upload anything
- Write `proposed_attachments` table with `status` = "pending"
- Print summary: `"Attachments pending: <count>"`

### 6. Integration in Main Flow

In `conflate.py`:
- If `--migrate-attachments` is set WITHOUT `--apply`:
  - Run in dry run mode (query attachments, write to `proposed_attachments` table with "pending" status)
- If `--migrate-attachments` is set WITH `--apply`:
  - After all updates and appends are complete, run attachment migration
  - Update `proposed_attachments` table with actual migration status
  - Print attachment migration summary

## Edge Cases

| Case | Handling |
|------|----------|
| Captured record has no attachments | Skip, no action needed |
| Attachment download fails | Log error, continue to next attachment |
| Attachment upload fails | Log error, continue to next attachment |
| Attachment name collision | Skip (don't overwrite existing attachment) |
| Layer doesn't support attachments | Log warning, skip migration |
| Very large attachment (>100MB) | Log warning, consider skipping or streaming |

## Test Criteria
- Attachments are queried from captured records correctly
- Migrated attachments appear on the authoritative record after upload
- Duplicate attachments (same name on auth record) are skipped
- Failed attachments are logged but don't abort the migration
- Dry run mode lists attachments with "pending" status
- Summary counts are accurate: migrated + skipped + failed = total attachments
- Captured record attachments are NOT deleted
