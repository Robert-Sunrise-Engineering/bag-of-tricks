# Phase 2: CLI & Initialization

## Objective
Build the command-line interface and initialization logic for `conflate.py`, including argument parsing, configuration loading, AGOL authentication, and layer metadata retrieval.

## Dependencies
- Phase 1 must be complete: `config.json`, `config.local.json`, and `setup-config.py` must exist

## Deliverables

### 1. CLI Argument Parsing

Parse command-line arguments using `argparse`:

| Argument | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `--layer` | string | Yes | N/A | Name of the layer to conflate (used for file naming) |
| `--apply` | flag | No | False | Write changes to AGOL instead of dry run |
| `--restore` | flag | No | False | Restore authoritative layer from backup |
| `--auto-open` | flag | No | False | Open review GeoPackage after dry run |
| `--migrate-attachments` | flag | No | False | Migrate attachments during apply |

Usage examples:
```bash
python conflate.py --layer "LayerName"                          # Dry run
python conflate.py --layer "LayerName" --apply                  # Apply changes
python conflate.py --layer "LayerName" --restore                # Restore from backup
python conflate.py --layer "LayerName" --auto-open              # Dry run + open review
python conflate.py --layer "LayerName" --apply --migrate-attachments  # Apply + attachments
```

### 2. Configuration Loading

Implement a `load_config()` function that:
1. Loads `config.json` from the project root (shared config with matching thresholds and paths)
2. Loads `config.local.json` from the project root (local config with credentials and URLs)
3. Validates that both files exist; if either is missing, print a clear error and exit with code 1
4. Returns a combined config object/dict containing:
   - `matching.threshold_ft` — distance threshold in feet for matching
   - `matching.ambiguity_pct` — percentage threshold for ambiguous matches
   - `paths.backup` — backup directory path
   - `paths.reports` — reports directory path
   - `agol.username` — AGOL username
   - `agol.password` — AGOL password
   - `captured_layer_url` — URL of captured (source) layer
   - `auth_layer_url` — URL of authoritative (destination) layer

### 3. AGOL Authentication

Implement an `authenticate_agol(config)` function that:
1. Uses `arcgis.gis.GIS` to connect to AGOL using username/password from config
2. Returns the authenticated `GIS` object
3. On failure (invalid credentials, network error), print a clear error and exit with code 1
4. Use a timeout parameter to avoid hanging indefinitely

### 4. Layer Metadata Retrieval

Implement a `get_layer_info(gis, layer_url)` function that:
1. Takes an authenticated `GIS` object and a layer URL
2. Extracts the item ID from the URL (the URL format is `https://<server>/arcgis/rest/services/<path>/FeatureServer/0`)
3. Fetches the layer's service info to get:
   - `layer_name` — name of the feature layer
   - `object_id_field` — name of the object ID field
   - `global_id_field` — name of the global ID field (if present)
   - `fields` — list of field definitions (name, type, length)
   - `has_attachments` — boolean indicating if the layer has attachments enabled
   - `use_global_ids` — boolean indicating if client-supplied GlobalIDs are supported
   - `geometry_type` — point, polyline, polygon, etc.
4. On failure, print a clear error with the URL and exit with code 1

### 5. Path Resolution

Implement a `resolve_paths(config, layer_name)` function that returns:
- `backup_dir` — full path to backup directory (from config + layer name)
- `backup_file` — `{layer_name}_backup_{YYYYMMDD_HHMMSS}.gpkg`
- `checkpoint_file` — `{layer_name}_checkpoint_{YYYYMMDD_HHMMSS}.json`
- `review_file` — `{layer_name}_conflation_review.gpkg`
- `report_file` — `{layer_name}_{YYYYMMDD_HHMMSS}.csv`

Timestamps use the format `YYYYMMDD_HHMMSS` (e.g., `20260714_143022`).

### 6. Main Entry Point

The `main()` function in `conflate.py` should:
1. Parse CLI arguments
2. If no `--layer` provided, print usage and exit with code 1
3. Load configuration via `load_config()`
4. Authenticate to AGOL via `authenticate_agol()`
5. Resolve output paths via `resolve_paths()`
6. Print a status message indicating which mode is active:
   - Dry run: `"Mode: DRY RUN — No changes will be written"`
   - Apply: `"Mode: APPLY — Changes will be written to AGOL"`
   - Restore: `"Mode: RESTORE — Will restore from backup"`
7. Print the layer name being processed

## Error Handling
- Missing config files: `"Required config file not found: <filename>"`
- Invalid JSON: `"Failed to parse <filename>: <error>"`
- AGOL auth failure: `"Could not authenticate to AGOL: <error>"`
- Layer not found: `"Layer not found or not accessible: <url>"`
- Exit code 1 on all fatal errors

## Test Criteria
- `--layer` argument is required; omitting it prints usage and exits with code 1
- `--apply`, `--restore`, `--auto-open`, `--migrate-attachments` flags are parsed correctly
- `load_config()` returns all expected fields from both config files
- `authenticate_agol()` connects to AGOL successfully with valid credentials
- `get_layer_info()` returns correct metadata for a known layer
- `resolve_paths()` generates correct file paths with layer name and timestamp
- Dry run mode prints the correct status message
- All errors print clear messages and exit with code 1
