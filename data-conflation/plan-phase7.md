# Phase 7: Dry Run Output

## Objective
Write the dry run review GeoPackage with 4 layers/tables, generate a CSV report, and print a summary. No changes are written to AGOL.

## Dependencies
- Phase 6 must be complete: collision resolution producing final match results
- Requires `config.json` paths for backup and reports directories

## Deliverables

### 1. Review GeoPackage Structure

Create a GeoPackage file at `backup/{layer_name}_conflation_review.gpkg` containing 4 spatial layers/tables:

#### Table 1: `current_state`
- Exact copy of the authoritative layer before any changes
- All fields preserved: OBJECTID, GlobalID, all attribute fields, geometry (WGS 84)
- No match metadata fields added
- This represents the authoritative data as it exists right now

#### Table 2: `proposed_updates`
- One row per matched record (match_type is "clean" or "ambiguous")
- Contains:
  - All attribute fields from the captured layer (non-null values only; null captured values → null in output)
  - Dual geometry columns:
    - `old_geometry` — original WGS 84 authoritative geometry (Point)
    - `new_geometry` — WGS 84 captured geometry (Point)
  - Extra metadata columns:
    - `distance_ft` — float, distance to match
    - `match_type` — string, "clean" or "ambiguous"
    - `action` — string, always "updated"
    - `captured_objectid` — int, source record OBJECTID
    - `label` — string, format: `"Updated: {distance_ft:.1f} ft from {match_type}"`

#### Table 3: `proposed_new`
- One row per unmatched record (match_type is "new")
- Contains:
  - All attribute fields from the captured layer (non-null values only)
  - Non-matching captured attributes concatenated into the `notes` field:
    - Format: `"FieldName: value | FieldName: value"`
    - Only include fields that exist in the authoritative layer schema but have null values in the captured record
    - Fields that don't exist in the authoritative schema are skipped
  - Geometry: the captured geometry (WGS 84)
  - Extra metadata columns:
    - `distance_ft` — null
    - `match_type` — string, always "new"
    - `action` — string, always "appended"
    - `captured_objectid` — int, source record OBJECTID
    - `label` — string, format: `"New: no match within {threshold} ft"`

#### Table 4: `proposed_attachments`
- One row per attachment on matched records (informational in dry run)
- Fields:
  - `captured_objectid` — int, source record OBJECTID
  - `auth_globalid` — string, target record GlobalID
  - `attachment_name` — string, name of the attachment
  - `attachment_size_bytes` — int, size in bytes
  - `attachment_type` — string, MIME type
  - `status` — string, always "pending" in dry run

### 2. Non-Matching Attributes → Notes Field

For records in `proposed_new`, identify non-matching captured attributes:
1. Compare captured layer field names against authoritative layer field names
2. For each field that exists in the authoritative schema but is null in the captured record:
   - Append to notes string: `"FieldName: value"`
   - Use ` | ` as separator between fields
3. Example: if `Address` is null but `Phone` has value, and both exist in auth schema:
   - Notes: `"Address: <null> | Phone: 555-1234"`
   - Actually: only include fields with values: `"Phone: 555-1234"`
   - Wait — re-read the spec: "Non-matching captured attributes → notes field" means attributes that are present in captured but not in auth, OR attributes that are null in captured but exist in auth. Let me re-read...
   - The spec says: "Non-matching captured attributes → notes field" for proposed_new
   - This means: fields that are in the captured data but don't have a corresponding match in the authoritative schema, OR fields where the captured value differs significantly
   - For practical purposes: concatenate all captured fields that have non-null values but don't exist in the authoritative schema, plus any fields that are null in captured but exist in auth schema
   - Format: `"FieldName: value"` for each, joined by ` | `

### 3. CSV Report

Write a CSV file at `reports/{layer_name}_{YYYYMMDD_HHMMSS}.csv` with columns:
```
layer, captured_objectid, auth_globalid, distance_ft, match_type, action, attachment_count, attachment_names
```

Each row corresponds to one captured record:
- `layer` — the layer name
- `captured_objectid` — OBJECTID of the captured record
- `auth_globalid` — GlobalID of matched auth record, or empty string if "new"
- `distance_ft` — distance to match, or empty string if "new"
- `match_type` — "clean", "ambiguous", or "new"
- `action` — "updated" or "appended"
- `attachment_count` — number of attachments on the captured record (0 if none)
- `attachment_names` — comma-separated list of attachment names, or empty string if none

### 4. Summary Output

Print a summary to console:
```
=== Conflation Report: <LayerName> ===
Matched (clean):     45
Matched (ambiguous):  3
New:                 12
Attachments pending: 58
Total:              60
Review: backup/<LayerName>_conflation_review.gpkg
Report: reports/<LayerName>_20260714_143022.csv
```

### 5. Auto-Open (`--auto-open`)

If the `--auto-open` flag is set:
1. After writing the review GeoPackage, attempt to open it
2. On Windows: use `os.startfile(review_path)`
3. On other platforms: use `subprocess.run(["xdg-open", review_path])`
4. If opening fails, log the path and print a warning: `"Could not auto-open review file: <path>"`

### 6. Integration in Main Flow

In `conflate.py`, after collision resolution (Phase 6):
1. Call `write_review_geopackage()` with match results, both GeoDataFrames, and config
2. Call `write_report_csv()` with match results and config
3. Print summary
4. If `--auto-open`, open the review file

## Test Criteria
- Review GeoPackage is created with exactly 4 tables
- `current_state` contains all records from the authoritative layer with all fields
- `proposed_updates` contains only matched records with dual geometry and correct metadata
- `proposed_new` contains only unmatched records with concatenated notes
- `proposed_attachments` contains one row per attachment on matched records
- CSV report has correct columns and one row per captured record
- Summary counts match the actual match results
- `--auto-open` opens the file on Windows
- Empty match types (e.g., no matches, no new records) produce empty tables (not missing tables)
