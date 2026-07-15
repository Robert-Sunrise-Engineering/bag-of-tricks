# Data Conflation Tool — Conversation Summary

## Context
- User has 7 geospatial layers collected with high-precision GPS
- Same layers exist in an authoritative ArcGIS Online (AGOL) source
- No metadata links between captured and authoritative data — matching is purely spatial
- All data is points, in WGS 84 (EPSG:4326)
- Sub-1000 features per layer
- Data location: Virgin Town, Utah (initially), but tool should work for any location
- Goal: match captured points to authoritative points by proximity, update matched records, append new ones

## Requirements Discovered During Discussion

### Matching
- Distance threshold: 9 ft (configurable)
- Ambiguity detection: second-nearest neighbor within 20% of nearest distance → flagged as "ambiguous"
- Purely spatial matching (no shared identifiers beyond layer name)
- Nearest-neighbor approach for point data

### Updates
- Matched records: update geometry AND non-null attributes from captured data
- Non-null captured attributes that don't exist in authoritative schema → concatenated into "notes" field
- Notes field format: "FieldName: value | FieldName: value" (pipe-separated, field name prepended)
- Unmatched (new) records: append with captured attributes, mismatches → notes
- Attachments: migrate from captured to authoritative, logged but NOT deleted from source
- Backup with GUIDs intact before any changes

### Input/Output
- AGOL layer URLs (not item IDs) — one URL per side (captured + authoritative)
- One layer processed at a time via CLI argument
- Dry run is the default behavior
- Dry run output must be reviewable in ArcGIS Pro

### Safety
- Backup authoritative layer to GeoPackage (preserves GUIDs)
- Per-layer isolation (failure in one layer doesn't cascade)
- Restore capability from backup
- Detailed logging with timestamps

### Review Output
- GeoPackage with 4 layers/tables:
  - `current_state` — exact copy of authoritative before changes
  - `proposed_updates` — matched records with old_geometry + new_geometry
  - `proposed_new` — new records to append
  - `proposed_attachments` — attachment migration details
- Timestamped CSV report per layer
- Visual clarity: dual geometry, labels, action field, symbology hints

### CLI
- `--layer "Name"` — process a layer (dry run by default)
- `--apply` — actually write changes
- `--restore` — restore from backup
- `--auto-open` — dry run + open review file in default handler

### Configuration
- `config.json` — shared config (committed to repo): thresholds, paths
- `config.local.json` — git-ignored: credentials, layer URLs
- `setup-config.py` — interactive config creator that validates URLs

### Technical
- UTM zone auto-detected from data centroid (no hardcoding)
- Reproject to UTM for distance calculations
- R-tree spatial index for nearest-neighbor queries
- Python with arcgis, geopandas, pyproj, rtree, pandas

## Open Questions (Answered)
1. Q: Multiple authoritative matches within threshold? A: Pick closest, flag if second-nearest within 20%
2. Q: UTM zone hardcoding? A: Auto-detect from centroid
3. Q: Single layer or multi-layer? A: One layer at a time via CLI
4. Q: Item IDs or URLs? A: Layer URLs
5. Q: Update only geometry or attributes too? A: Both (Option B)
6. Q: Delete attachments after migration? A: No, keep source attachments
7. Q: Include attachment info in dry run? A: Yes, in review GeoPackage + CSV
