# Data Conflation Tool — Implementation Plan

## Overview
A reusable Python tool for conflating geospatial point data from a captured source to an authoritative ArcGIS Online (AGOL) Feature Layer. Matches are determined by spatial proximity, matched records are updated (geometry + attributes), and unmatched records are appended as new features.

## Architecture

```
config.json                # Shared config (committed to repo)
config.local.json          # Credentials + URLs (git-ignored)
.gitignore
setup-config.py            # Interactive config creator
conflate.py                # Main conflation script
requirements.txt
├── backup/                # git-ignored
│   ├── {layer}_backup_{YYYYMMDD_HHMMSS}.gpkg
│   ├── {layer}_checkpoint_{YYYYMMDD_HHMMSS}.json
│   └── {layer}_conflation_review.gpkg
└── reports/               # git-ignored
    └── {layer}_{YYYYMMDD_HHMMSS}.csv
```

## Dependencies

```
arcgis>=2.1.0
geopandas>=0.14.0
pyproj>=3.6.0
pandas>=2.0.0
```

## Configuration

### config.json (committed)
```json
{
  "matching": {
    "threshold_ft": 9,
    "ambiguity_pct": 20
  },
  "paths": {
    "backup": "backup/",
    "reports": "reports/"
  }
}
```

### config.local.json (git-ignored)
```json
{
  "agol": {
    "username": "...",
    "password": "..."
  },
  "captured_layer_url": "https://.../FeatureServer/0",
  "auth_layer_url": "https://.../FeatureServer/0"
}
```

## CLI Interface

```bash
python conflate.py --layer "LayerName"              # Dry run (default)
python conflate.py --layer "LayerName" --apply       # Write changes
python conflate.py --layer "LayerName" --restore     # Restore from backup
python conflate.py --layer "LayerName" --auto-open   # Dry run + open review file
python conflate.py --layer "LayerName" --apply --migrate-attachments  # Apply + attachments
```

## Setup Script (`setup-config.py`)

1. Prompt for AGOL username
2. Prompt for AGOL password (masked input)
3. Prompt for captured layer URL
4. Prompt for authoritative layer URL
5. Validate both URLs by fetching layer info
6. Display layer names and feature counts
7. Write `config.local.json`

## Main Script (`conflate.py`)

### 1. Initialization
- Load `config.json` and `config.local.json`
- Parse CLI arguments (`--layer`, `--apply`, `--restore`, `--auto-open`, `--migrate-attachments`)
- Authenticate to AGOL using credentials from config
- Determine output paths based on layer name and timestamp

### 2. Schema Validation
- Verify `notes` field exists on authoritative layer schema
- Record `notes` field max length
- If `notes` field missing → abort with clear error

### 3. Backup
- Export authoritative layer to `backup/{layer}_backup_{YYYYMMDD_HHMMSS}.gpkg`
- Preserve all fields including `OBJECTID` and `GlobalID`
- Abort if backup fails

### 4. Data Loading
- Load captured layer from `captured_layer_url`
- Load authoritative layer from `auth_layer_url`
- Convert to GeoDataFrames
- Skip records with null/empty geometries (log warning)

### 5. CRS Handling
- Auto-detect UTM zone from data centroid:
  ```python
  zone = int(np.floor((centroid_lon + 180) / 6)) + 1
  epsg = 32600 + zone if lat >= 0 else 32700 + zone
  ```
- Reproject both datasets to detected UTM zone (transient, for distance calculations only)
- Store original WGS 84 geometries for output
- Discard UTM reprojected data after matching

### 6. Spatial Indexing
- Build spatial index using `geopandas.sindex` on authoritative geometries (in UTM)

### 7. Matching
For each captured point:
1. Query 2 nearest authoritative points from spatial index
2. Calculate distances in feet (from UTM coordinates)
3. Classify:
   - **Clean match**: nearest ≤ threshold, second-nearest > threshold OR second-nearest distance > (nearest × (1 + ambiguity_pct/100))
   - **Ambiguous match**: nearest ≤ threshold AND second-nearest ≤ threshold AND second-nearest distance ≤ nearest × (1 + ambiguity_pct/100)
   - **New**: nearest > threshold or no authoritative points found
4. Store classification with metadata (GlobalID, distance, match_type, old/new geometry)

### 7a. Global Collision Resolution
After per-point matching completes:
1. Track claimed authoritative GlobalIDs across all captured points
2. Detect many-to-one collisions (two different captured points → same authoritative)
3. Resolve: closest captured point wins, other goes to `proposed_new`
4. Flag all collisions in dry-run output

### 8. Dry Run Output
Write review GeoPackage with 4 layers/tables:

#### `current_state`
- Exact copy of authoritative layer before changes
- All fields preserved (OBJECTID, GlobalID, attributes)

#### `proposed_updates`
- Matched records with dual geometry:
  - `old_geometry` — original authoritative geometry (WGS 84)
  - `new_geometry` — captured geometry (WGS 84)
- Attributes from captured (non-null only)
- Extra fields:
  - `distance_ft` — distance to match
  - `match_type` — "clean" or "ambiguous"
  - `action` — "updated"
  - `captured_objectid` — source record OBJECTID
  - `label` — "Updated: {distance_ft:.1f} ft from {match_type}"

#### `proposed_new`
- New records to append
- Attributes from captured (non-null only)
- Non-matching captured attributes → `notes` field:
  - Format: "FieldName: value | FieldName: value"
- Extra fields:
  - `distance_ft` — null
  - `match_type` — "new"
  - `action` — "appended"
  - `captured_objectid` — source record OBJECTID
  - `label` — "New: no match within {threshold} ft"

#### `proposed_attachments`
- Attachment migration details (informational in dry-run)
- Fields:
  - `captured_objectid`
  - `auth_globalid`
  - `attachment_name`
  - `attachment_size_bytes`
  - `attachment_type`
  - `status` — "pending" (dry run) or "migrated" / "skipped" (apply)

#### Report CSV
- Path: `reports/{layer}_{YYYYMMDD_HHMMSS}.csv`
- Columns: `layer, captured_objectid, auth_globalid, distance_ft, match_type, action, attachment_count, attachment_names`

### 9. Apply Changes (`--apply`)
#### Checkpoint Resume
- If `backup/{layer}_checkpoint_{timestamp}.json` exists:
  - Prompt: "Resume from previous run? [Y/n]"
  - If yes: load checkpoint, skip already-applied items
  - If no: start fresh (treat as new run)

#### Update Matched Records
One-at-a-time (or small batches of ~50):
1. For each matched record (skip if already checkpointed):
   - Update geometry to captured coordinates
   - For each non-null captured attribute that exists in authoritative schema:
     - Update the attribute value
   - Non-matching captured attributes → concatenate into `notes` field (rebuilt fresh, not appended)
   - Truncate to `notes` max length if needed (log warning)
   - Use `edit_features()` with GlobalID to preserve record identity
   - On success: append GlobalID to checkpoint file
   - On failure: log error, configurable continue/abort

#### Migrate Attachments (`--migrate-attachments`)
One-at-a-time, independent failures:
1. For each matched record:
   - Query attachments from captured record (by OBJECTID)
   - For each attachment:
     - Download attachment data
     - Add to authoritative record (by GlobalID)
     - Log: attachment name, size, status
     - Dedup by attachment name (skip if already exists)
2. Do NOT delete attachments from captured record

#### Append New Records
One-at-a-time (or small batches of ~50):
1. For each new record (skip if already checkpointed):
   - Build attribute dict from captured data (non-null only)
   - Non-matching captured fields → concatenate into `notes` field (rebuilt fresh)
   - Truncate to `notes` max length if needed (log warning)
   - Use `edit_features()` with `appends` parameter
   - On success: append GlobalID to checkpoint file
   - On failure: log error, configurable continue/abort

#### Cleanup
- On success: delete checkpoint file
- On failure: checkpoint preserved for resume

### 10. Restore (`--restore`)
1. List available timestamped backups in `backup/`
2. User selects which backup to restore from
3. Validate that the authoritative layer supports client-supplied GlobalIDs (`use_global_ids`)
4. Load selected backup GeoPackage
5. Replace authoritative layer with backup data
6. Log completion

### 11. Summary Output
```
=== Conflation Report: LayerName ===
Matched (clean):    45
Matched (ambiguous): 3
New:                12
Attachments pending: 58
Total:              60
Review: backup/LayerName_conflation_review.gpkg
Report: reports/LayerName_20260714_143022.csv
```

### 12. Auto-Open (`--auto-open`)
- Log path to review GeoPackage
- If flag set: attempt to open review file using `os.startfile()` (Windows) or `subprocess` (cross-platform)

## Edge Cases

| Case | Handling |
|------|----------|
| Empty captured layer | Skip layer, log warning |
| Empty authoritative layer | Append all captured as new |
| No matches found | All captured appended as new |
| Ambiguous matches | Pick closest, flag in report |
| Schema mismatch | Mismatched fields → notes field |
| `notes` field missing | Abort with clear error |
| `notes` field truncation | Truncate with warning |
| Attachment upload failure | Log error, continue (independent) |
| AGOL timeout | Retry with exponential backoff |
| Backup failure | Abort conflation, log error |
| Layer not found | Log error, skip layer |
| Null/empty geometry | Skip with warning |
| Many-to-one collision | Closest wins, other → proposed_new |
| Apply crash mid-way | Checkpoint preserved, resume on next run |

## Workflow

```
1. Run setup-config.py to create config.local.json
2. Run: python conflate.py --layer "LayerName"
   → Creates review GeoPackage + report CSV
   → Prints summary
3. Open backup/LayerName_conflation_review.gpkg in ArcGIS Pro
   → Compare proposed_updates against current data
   → Verify proposed_new are actually new features
   → Check notes field for data loss
   → Review proposed_attachments table
4. If satisfied:
   → Run: python conflate.py --layer "LayerName" --apply
   → If attachments needed: add --migrate-attachments
5. If apply crashes mid-way:
   → Run again: python conflate.py --layer "LayerName" --apply
   → Prompts to resume from checkpoint
6. If not satisfied:
   → Adjust threshold in config.json, re-run dry mode
   → Or restore: python conflate.py --layer "LayerName" --restore
```

## Error Handling

- All AGOL operations wrapped in try/except
- Retry with exponential backoff for network errors
- Detailed logging to console and optional log file
- Each layer processed independently (failure doesn't cascade)
- Timestamped backups for full recovery
- Lightweight checkpoint for resume capability
- Schema validation before any writes
- Notes field length validation at write time
