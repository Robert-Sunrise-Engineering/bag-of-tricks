# Manual Test Suite — Phase 1, 2 & 3

## Prerequisites

Before running any tests, ensure:

1. **Python environment** — Python 3.9+ installed with dependencies from `requirements.txt` (includes `geopandas`, `pyproj`)
2. **AGOL credentials** — Valid username/password for `arcgis.com`
3. **Two Feature Layer URLs** — Full `FeatureServer/0` endpoints (can use the same layer tested twice, or two different ones)
4. **Clean workspace** — No existing `config.local.json` for Phase 1 tests; no leftover backups for Phase 2 tests
5. **Layers with valid geometries** — For Phase 3 tests, at least one layer must have features with valid (non-null) geometries

---

## Part A: Phase 1 — Setup Script (`setup_config.py`)

### Test 1.1: Happy Path (Full Flow)
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py`
2. Enter valid AGOL username
3. Enter valid AGOL password
4. Enter valid captured layer URL
5. Enter valid authoritative layer URL
6. Wait for validation

**Expected output:**
```
========================================
Data Conflation Configuration Setup
========================================

Step 1: AGOL Credentials
----------------------------------------
AGOL username: [entered]
AGOL password: ********

Step 2: Authenticating to AGOL...
----------------------------------------
  Connecting to arcgis.com (AGOL)...
  Authenticated as: <username>

Step 3: Layer URLs
----------------------------------------
Captured layer URL (FeatureServer/0 endpoint): [entered]
Authoritative layer URL (FeatureServer/0 endpoint): [entered]

Step 4: Validating Layers
----------------------------------------

Validating Captured Layer...
  Layer name: <captured_layer_name>
  Layer type: Feature Layer

Validating Authoritative Layer...
  Layer name: <auth_layer_name>
  Layer type: Feature Layer

========================================
Layer Information
========================================

Captured Layer:
  Name: <captured_layer_name>
  Feature count: <number>
  Fields: <field_list>

Authoritative Layer:
  Name: <auth_layer_name>
  Feature count: <number>
  Fields: <field_list>

========================================
Writing Configuration
========================================

Configuration written to <path>\config.local.json

Setup complete!
```

**Verification:**
```powershell
# Check file exists and structure
$config = Get-Content config.local.json | ConvertFrom-Json
$config.agol.username    # should match entered username
$config.agol.password    # should match entered password
$config.captured_layer_url  # should match entered URL
$config.auth_layer_url      # should match entered URL
```

---

### Test 1.2: Overwrite Protection (Default No)
**Setup:** `config.local.json` already exists (from Test 1.1)

**Steps:**
1. Run: `python setup_config.py`
2. When prompted `"config.local.json already exists. Overwrite? [y/N]:"` → press Enter (no input)

**Expected:**
- Output: `"Aborted. No changes made."`
- Script exits with code 0
- Original `config.local.json` content is unchanged

**Verification:**
```powershell
# Confirm file unchanged
$config = Get-Content config.local.json | ConvertFrom-Json
# Should still have original values
```

---

### Test 1.3: Overwrite Accept
**Setup:** `config.local.json` exists

**Steps:**
1. Run: `python setup_config.py`
2. When prompted → type `y` and Enter
3. Enter new valid credentials and URLs

**Expected:**
- New `config.local.json` written with new values
- Full flow completes normally
- `"Setup complete!"` printed

**Verification:**
```powershell
$config = Get-Content config.local.json | ConvertFrom-Json
# Should contain the NEW values entered in this run
```

---

### Test 1.4: Invalid Credentials
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py`
2. Enter a wrong/invalid username and password

**Expected:**
- Output: `"Could not authenticate to AGOL. Please check your credentials."`
- Error details printed
- Script exits with code 1

---

### Test 1.5: Both URLs Invalid (Abort After Retries)
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py`
2. Enter valid credentials
3. Enter a fake URL (e.g., `https://services.arcgis.com/fake/FeatureServer/0`)
4. When prompted `"Retry? [y/N]:"` → type `y`
5. Enter another fake URL
6. When prompted `"Retry? [y/N]:"` → type `n`

**Expected:**
- First URL fails with: `"URL does not point to a valid Feature Layer: <url>"`
- Second URL fails similarly
- Output: `"One or both URLs are invalid. Aborting setup."`
- Script exits with code 1

---

### Test 1.6: One Valid, One Invalid URL
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py`
2. Enter valid credentials
3. Enter a valid layer URL (captured)
4. Enter a fake URL (auth)
5. When prompted `"Retry? [y/N]:"` → type `y`
6. Enter another fake URL
7. When prompted `"Retry? [y/N]:"` → type `n`

**Expected:**
- First (captured) layer validates successfully
- Second (auth) layer fails with retry prompts
- Output: `"One or both URLs are invalid. Aborting setup."`
- Script exits with code 1

---

### Test 1.7: Wrong Layer Type [REMOVED — covered by pytest `test_wrong_layer_type_rejected`]

### Test 1.8: Empty URL Handling
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py`
2. Enter valid credentials
3. Press Enter for captured layer URL (empty)
4. Press Enter again (empty again)
5. Enter a valid captured layer URL
6. Enter a valid auth layer URL

**Expected:**
- `"URL cannot be empty. Please try again."` printed for each empty input
- Script continues to prompt until valid URL entered
- Setup completes normally

---

### Test 1.9: Config File Permissions
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py` with valid inputs (full happy path)
2. After completion, check file permissions

**Expected:**
- `config.local.json` exists
- File permissions are restrictive (0o600 — owner read/write only)

**Verification (Windows):**
```powershell
# Check file ACL — should not be world-readable
Get-Acl config.local.json | Format-List
```

---

### Test 1.10: Existing config.local.json Content Preservation on Failure
**Setup:** `config.local.json` exists with valid data

**Steps:**
1. Run: `python setup_config.py`
2. Type `y` to overwrite
3. Enter valid credentials
4. Enter a valid captured layer URL
5. Enter an invalid auth layer URL
6. Decline retry (`n`)

**Expected:**
- Output: `"One or both URLs are invalid. Aborting setup."`
- Script exits with code 1
- `config.local.json` is NOT modified (original content preserved)

**Verification:**
```powershell
# Original content should still be intact
$config = Get-Content config.local.json | ConvertFrom-Json
# Compare with original values
# If retry was used, verify the config contains the retry URL, not the original invalid URL
```

---

## Part B: Phase 2 — CLI & Initialization (`conflate.py`)

### Prerequisites for Phase 2 tests
- `config.local.json` must exist with valid credentials and layer URLs
- `config.json` must exist with valid JSON (matching thresholds and paths)
- Layers referenced in config must be accessible

---

### Test 2.1: Missing --layer Argument
**Setup:** Valid `config.local.json` and `config.json`

**Steps:**
1. Run: `python conflate.py`

**Expected:**
- Argparse prints usage information
- Script exits with code 2

---

### Test 2.2: Help Flag
**Setup:** Any config

**Steps:**
1. Run: `python conflate.py --help`

**Expected:**
- Usage info printed with all arguments and examples
- Script exits with code 0
- Output includes: `--layer`, `--apply`, `--restore`, `--auto-open`, `--migrate-attachments`

---

### Test 2.3: Dry Run Mode
**Setup:** Valid config, accessible layers

**Steps:**
1. Run: `python conflate.py --layer "TestLayer"`

**Expected:**
- Output: `"Mode: DRY RUN — No changes will be written"`
- Output: `"Layer: TestLayer"`
- Script authenticates to AGOL successfully
- Script exits normally (code 0) — no changes written

---

### Test 2.4: Apply Mode
**Setup:** Valid config, accessible layers

**Steps:**
1. Run: `python conflate.py --layer "TestLayer" --apply`

**Expected:**
- Output: `"Mode: APPLY — Changes will be written to AGOL"`
- Output: `"Layer: TestLayer"`
- Script authenticates to AGOL successfully
- Script exits normally (code 0)

---

### Test 2.5: Restore Mode
**Setup:** Valid config, accessible layers

**Steps:**
1. Run: `python conflate.py --layer "TestLayer" --restore`

**Expected:**
- Output: `"Mode: RESTORE — Will restore from backup"`
- Output: `"Layer: TestLayer"`
- Script authenticates to AGOL successfully
- Script exits normally (code 0)

---

### Test 2.6: Combined Flags
**Setup:** Valid config, accessible layers

**Steps:**
1. Run: `python conflate.py --layer "TestLayer" --apply --migrate-attachments --auto-open`

**Expected:**
- Output: `"Mode: APPLY — Changes will be written to AGOL"`
- Output: `"Auto-open review file after dry run: enabled"`
- Output: `"Migrate attachments: enabled"`
- Output: `"Layer: TestLayer"`

---

### Test 2.7: Missing `config.json`
**Setup:** Rename or remove `config.json` temporarily

**Steps:**
1. Run: `python conflate.py --layer "TestLayer"`

**Expected:**
- Output: `"Required config file not found: config.json"`
- Script exits with code 1

---

### Test 2.8: Missing `config.local.json`
**Setup:** Rename or remove `config.local.json` temporarily

**Steps:**
1. Run: `python conflate.py --layer "TestLayer"`

**Expected:**
- Output: `"Required config file not found: config.local.json"`
- Script exits with code 1

---

### Test 2.9: Invalid JSON in `config.json`
**Setup:** Write invalid JSON to `config.json` (e.g., `{ invalid }`)

**Steps:**
1. Run: `python conflate.py --layer "TestLayer"`

**Expected:**
- Output: `"Failed to parse config.json: <parse error>"`
- Script exits with code 1

---

### Test 2.10: Invalid JSON in `config.local.json`
**Setup:** Write invalid JSON to `config.local.json` (e.g., `{ invalid }`)

**Steps:**
1. Run: `python conflate.py --layer "TestLayer"`

**Expected:**
- Output: `"Failed to parse config.local.json: <parse error>"`
- Script exits with code 1

---

### Test 2.11: Invalid AGOL Credentials
**Setup:** Modify `config.local.json` with wrong username/password

**Steps:**
1. Run: `python conflate.py --layer "TestLayer"`

**Expected:**
- Output: `"Could not authenticate to AGOL: <error>"`
- Script exits with code 1

---

### Test 2.12: Missing Username in Config
**Setup:** Modify `config.local.json` to remove `username` from `agol` section

**Steps:**
1. Run: `python conflate.py --layer "TestLayer"`

**Expected:**
- Output: `"Could not authenticate to AGOL: username or password missing from config"`
- Script exits with code 1

---

### Test 2.13: Missing Password in Config
**Setup:** Modify `config.local.json` to remove `password` from `agol` section

**Steps:**
1. Run: `python conflate.py --layer "TestLayer"`

**Expected:**
- Output: `"Could not authenticate to AGOL: username or password missing from config"`
- Script exits with code 1

---

### Test 2.14: Path Resolution — Default Paths
**Setup:** Valid config with default paths (`backup/`, `reports/`)

**Steps:**
1. Run: `python conflate.py --layer "PathTest" --apply` (will fail at AGOL layer fetch, but paths resolve first)
2. OR: Inspect code behavior by running with a mock

**Expected (from `resolve_paths`):**
- `backup_file` format: `PathTest_backup_YYYYMMDD_HHMMSS.gpkg`
- `checkpoint_file` format: `PathTest_checkpoint_YYYYMMDD_HHMMSS.json`
- `review_file` format: `PathTest_conflation_review.gpkg`
- `report_file` format: `PathTest_YYYYMMDD_HHMMSS.csv`

**Timestamp verification:**
```powershell
# Timestamp should match current date/time
Get-Date -Format "yyyyMMdd_HHmmss"
```

---

### Test 2.15: Path Resolution — Custom Paths
**Setup:** Modify `config.json` to use custom paths:
```json
{
  "matching": { "threshold_ft": 9, "ambiguity_pct": 20 },
  "paths": { "backup": "custom_backup/", "reports": "custom_reports/" }
}
```

**Steps:**
1. Run: `python conflate.py --layer "CustomPath" --apply`

**Expected:**
- Backup files use `custom_backup/` directory
- Report files use `custom_reports/` directory

---

### Test 2.16: Path Resolution — Paths Without Trailing Slashes
**Setup:** Modify `config.json` to use paths without trailing slashes:
```json
{
  "paths": { "backup": "backup", "reports": "reports" }
}
```

**Steps:**
1. Run: `python conflate.py --layer "NoSlash" --apply`

**Expected:**
- Paths resolve correctly despite no trailing slashes
- No errors related to path formatting

---

### Test 2.17: Layer Not Accessible [SUPERSEDED — Phase 3]
**Status:** Superseded. The layer loading tests in Part C (Phase 3) cover this functionality. See tests 3.1–3.3 for data loading behavior.

---

### Test 2.18: Auto-Open Flag
**Setup:** Valid config, accessible layers

**Steps:**
1. Run: `python conflate.py --layer "TestLayer" --auto-open`

**Expected:**
- Output: `"Auto-open review file after dry run: enabled"`
- Script proceeds with dry run
- On Windows: attempts to open review file via `os.startfile()`
- On other platforms: attempts via `subprocess`

---

### Test 2.19: Layer Name with Special Characters
**Setup:** Valid config, accessible layers

**Steps:**
1. Run: `python conflate.py --layer "Test Layer (Production)" --apply`

**Expected:**
- Output: `"Layer: Test Layer (Production)"`
- File paths handle special characters correctly (quoted/escaped as needed)
- No path resolution errors

---

### Test 2.20: Multiple Runs — Timestamp Uniqueness
**Status:** Moved to Part D (Test 4.8). Backup creation is now implemented in Phase 4.

---

## Part C: Phase 3 — Data Loading & CRS Handling (`conflate.py`)

### Prerequisites for Phase 3 tests
- `config.local.json` must exist with valid credentials and layer URLs
- `config.json` must exist with valid JSON (matching thresholds and paths)
- Layers referenced in config must have features with valid geometries (at least one layer)
- Python environment must have `geopandas`, `pyproj` installed

---

### Test 3.1: Load Layer — Happy Path
**Setup:** Valid config, layer with known features

**Steps:**
1. Run: `python conflate.py --layer "TestLayer"` (dry run, but data loads)
2. Check log output for feature counts

**Expected:**
- Log: `"Loaded <n> features from <layer_name>"`
- Layer loads as GeoDataFrame with all attribute fields (OBJECTID, GlobalID, etc.)
- CRS is EPSG:4326 (WGS 84)
- Geometry column contains valid geometries
- Feature count matches AGOL feature count

---

### Test 3.2: Load Layer — Null/Empty Geometries Skipped
**Setup:** A layer known to have some records with null/empty geometries (or create a test layer)

**Steps:**
1. Run conflation with the layer
2. Check log output

**Expected:**
- Records with null/empty geometries are excluded from the GeoDataFrame
- Warning logged for each skipped record: `"Skipping record OBJECTID=<id>: null/empty geometry"`
- Summary warning: `"Skipped <n> records with null/empty geometry from <layer_name>"`

---

### Test 3.3: Load Layer — Empty Layer
**Setup:** An empty feature layer (0 features) or a layer where all geometries are null

**Steps:**
1. Run conflation targeting the empty layer

**Expected:**
- Empty GeoDataFrame returned (no crash)
- Warning logged: `"Layer <name> has no features with valid geometry"`

---

### Test 3.4: UTM Zone Detection — Northern Hemisphere
**Setup:** Valid config, layer in a known northern location

**Steps:**
1. Run conflation with a layer in a known northern location (e.g., New York area)
2. Check log output for UTM zone detection

**Expected:**
- Log contains: `"Detected UTM zone: EPSG:32618"` (for NYC-area)
- UTM coordinates are in meters, not degrees

---

### Test 3.5: UTM Zone Detection — Southern Hemisphere
**Setup:** Valid config, layer in a known southern location

**Steps:**
1. Run conflation with a layer in a known southern location (e.g., Sydney, Australia area)
2. Check log output for UTM zone detection

**Expected:**
- Log contains: `"Detected UTM zone: EPSG:32756"` (for Sydney-area)
- Southern hemisphere uses 327xx EPSG codes

---

### Test 3.6: CRS Reprojection — Meter Verification
**Setup:** Valid config, any layer with features

**Steps:**
1. Run conflation
2. After UTM reprojection, verify coordinates are in meters (not degrees)

**Expected:**
- Reprojected coordinates are large numbers (meters from origin), not small decimal values (degrees)
- For a NYC-area layer: X coordinate should be ~500,000+ meters, Y should be ~4,500,000+ meters

---

### Test 3.7: Full `prepare_data()` Workflow
**Setup:** Valid config with two different layers (captured and authoritative)

**Steps:**
1. Run conflation with both layers having valid data
2. Check log output and verify both layers loaded

**Expected:**
- Log: `"Loaded <n> features from <captured_layer_name>"`
- Log: `"Loaded <n> features from <auth_layer_name>"`
- Log: `"Detected UTM zone: EPSG:<code>"`
- Both layers loaded in WGS 84 AND UTM
- WGS 84 GeoDataFrames retain original degree coordinates
- UTM GeoDataFrames have meter coordinates

---

### Test 3.8: Empty Auth Layer Fallback
**Setup:** Valid config where authoritative layer is empty but captured layer has data

**Steps:**
1. Run conflation

**Expected:**
- UTM zone detected from captured layer (fallback)
- Both GeoDataFrames created without error
- No crash or exception

---

### Test 3.9: Both Layers Empty
**Setup:** Valid config where both layers are empty

**Steps:**
1. Run conflation

**Expected:**
- Defaults to EPSG:32618 (UTM 18N)
- Both empty GeoDataFrames created
- No crash or exception

---

## Execution Order (Recommended)

Run in this sequence for efficient credential reuse:

### Phase 1 (Setup Script)
| # | Test | Purpose | Requires |
|---|------|---------|----------|
| 1 | 1.4 | Invalid credentials (quick fail) | None |
| 2 | 1.8 | Empty URL handling | None |
| 3 | 1.1 | **Happy path** (full flow, creates config) | Valid credentials + URLs |
| 4 | 1.2 | Overwrite protection | From Test 1.1 |
| 5 | 1.3 | Overwrite accept | From Test 1.2 |
| 6 | 1.5 | Both URLs invalid | Valid credentials |
| 7 | 1.6 | One valid, one invalid | Valid credentials |
| 8 | 1.9 | Config file permissions | From Test 1.1 |
| 9 | 1.10 | Content preservation on failure | From Test 1.1 |

### Phase 2 (CLI)
| # | Test | Purpose | Requires |
|---|------|---------|----------|
| 1 | 2.1 | Missing --layer (argparse test) | Valid config files |
| 2 | 2.2 | Help flag | None |
| 3 | 2.7 | Missing config.json | None |
| 4 | 2.8 | Missing config.local.json | None |
| 5 | 2.9 | Invalid config.json JSON | None |
| 6 | 2.10 | Invalid config.local.json JSON | None |
| 7 | 2.11 | Invalid AGOL credentials | None |
| 8 | 2.12 | Missing username | None |
| 9 | 2.13 | Missing password | None |
| 10 | 2.3 | **Dry run mode** (full init) | Valid config + URLs |
| 11 | 2.4 | Apply mode | From Test 2.3 |
| 12 | 2.5 | Restore mode | From Test 2.3 |
| 13 | 2.6 | Combined flags | From Test 2.3 |
| 14 | 2.14 | Default path resolution | From Test 2.3 |
| 15 | 2.15 | Custom path resolution | Modify config.json |
| 16 | 2.16 | No-trailing-slash paths | Modify config.json |
| 17 | 2.17 | Layer not accessible [SUPERSEDED] | See Phase 3 tests |
| 18 | 2.18 | Auto-open flag | From Test 2.3 |
| 19 | 2.19 | Special characters in layer name | From Test 2.3 |
| 20 | 2.20 | Timestamp uniqueness [DEFERRED] | From Test 2.3 |

### Phase 3 (Data Loading & CRS)
| # | Test | Purpose | Requires |
|---|------|---------|----------|
| 1 | 3.2 | Null geometry skipping (quick fail) | Layer with null geoms |
| 2 | 3.3 | Empty layer handling | Empty layer |
| 3 | 3.1 | **Happy path** (full data load) | Valid layer with data |
| 4 | 3.4 | UTM detection — northern | Northern hemisphere layer |
| 5 | 3.5 | UTM detection — southern | Southern hemisphere layer |
| 6 | 3.6 | Meter coordinate verification | Any layer with data |
| 7 | 3.7 | Full workflow (both layers) | Two valid layers |
| 8 | 3.8 | Empty auth fallback | Empty auth layer |
| 9 | 3.9 | Both empty fallback | Both layers empty |

---

## Part D: Phase 4 — Schema Validation & Backup (`conflate.py`)

### Prerequisites for Phase 4 tests
- `config.local.json` must exist with valid credentials and layer URLs
- `config.json` must exist with valid JSON (matching thresholds and paths)
- Layers referenced in config must have a `notes` field (for validation tests)
- `backup/` directory should be clean (no leftover backup files)
- Python environment must have `geopandas`, `pyproj` installed

---

### Test 4.1: Schema Validation — Happy Path
**Setup:** Valid config, authoritative layer has a `notes` field

**Steps:**
1. Run: `python conflate.py --layer "TestLayer"` (dry run)

**Expected output:**
```
Mode: DRY RUN — No changes will be written
Layer: TestLayer
Captured layer: <captured_name>
Authoritative layer: <auth_name>
Loading layers...
Loaded <n> captured features
Loaded <n> authoritative features
Validating schema...
Schema validation passed: notes_max_length=<value>
Creating backup...
Backup created: backup/<Layer>_backup_<YYYYMMDD_HHMMSS>.gpkg
Phase 4 complete. Ready for matching (Phase 5).
```

**Verification:**
```powershell
# Check backup file exists
Test-Path backup\<Layer>_backup_*.gpkg  # should be True
```

---

### Test 4.2: Schema Validation — Missing Notes Field
**Setup:** Valid config, authoritative layer without a `notes` field (create a test layer or use one that lacks it)

**Steps:**
1. Run: `python conflate.py --layer "NoNotesLayer"`

**Expected:**
- Output: `"FATAL: Authoritative layer 'NoNotesLayer' is missing the required 'notes' field. Aborting."`
- Script exits with code 1
- No backup file is created

**Verification:**
```powershell
# No new backup files should exist
Get-ChildItem backup\NoNotesLayer_backup_*  # should return nothing
```

---

### Test 4.3: Backup File Verification
**Setup:** Valid config, layer with `notes` field and data

**Steps:**
1. Run: `python conflate.py --layer "VerifyBackup"` (dry run)
2. Find the most recent backup file
3. Verify the backup with Python:

```python
import geopandas as gpd
gdf = gpd.read_file("backup/VerifyBackup_backup_*.gpkg")
print(f"Rows: {len(gdf)}")
print(f"Columns: {list(gdf.columns)}")
print(f"CRS: {gdf.crs}")
assert "OBJECTID" in gdf.columns
assert "GlobalID" in gdf.columns
assert "notes" in gdf.columns
assert gdf.crs.to_epsg() == 4326
```

**Expected:**
- Backup file exists and is readable
- Feature count matches the authoritative layer
- All fields preserved (OBJECTID, GlobalID, notes, and any other fields)
- CRS is EPSG:4326 (WGS 84)

---

### Test 4.4: Backup Directory Auto-Creation
**Setup:** Valid config, `backup/` directory deleted

**Steps:**
1. Delete the `backup/` directory: `Remove-Item backup -Recurse -Force`
2. Run: `python conflate.py --layer "AutoDirTest"` (dry run)

**Expected:**
- `backup/` directory is automatically recreated
- Backup file is created inside it
- No errors related to missing directory

**Verification:**
```powershell
Test-Path backup\AutoDirTest_backup_*.gpkg  # should be True
```

---

### Test 4.5: Checkpoint File Structure
**Setup:** No checkpoint file exists

**Steps:**
1. Create a test checkpoint file manually:
```json
{
  "timestamp": "20260714_143022",
  "layer": "TestLayer",
  "applied_updates": ["{gid1}", "{gid2}"],
  "applied_new": ["{gid3}"]
}
```

2. Verify with Python:
```python
from conflate import load_checkpoint, checkpoint_add_update, checkpoint_add_new

# Load and verify
data = load_checkpoint("backup/test_checkpoint.json")
assert data["layer"] == "TestLayer"
assert len(data["applied_updates"]) == 2
assert len(data["applied_new"]) == 1

# Test append
checkpoint_add_update("backup/test_checkpoint.json", "{gid4}")
data = load_checkpoint("backup/test_checkpoint.json")
assert data["applied_updates"] == ["{gid1}", "{gid2}", "{gid4}"]

# Test new append
checkpoint_add_new("backup/test_checkpoint.json", "{gid5}")
data = load_checkpoint("backup/test_checkpoint.json")
assert data["applied_new"] == ["{gid3}", "{gid5}"]

# Test nonexistent file
assert load_checkpoint("backup/nonexistent.json") is None
```

**Expected:**
- All checkpoint operations work correctly
- File structure matches the spec
- Nonexistent file returns `None`

---

### Test 4.6: Empty Layer Backup
**Setup:** Valid config, empty authoritative layer (0 features)

**Steps:**
1. Run: `python conflate.py --layer "EmptyLayer"` (dry run)

**Expected:**
- Schema validation passes (notes field exists, even with 0 rows)
- Backup GPKG is created
- Backup file is valid but contains 0 rows

**Verification:**
```python
import geopandas as gpd
gdf = gpd.read_file("backup/EmptyLayer_backup_*.gpkg")
assert len(gdf) == 0
assert "notes" in gdf.columns  # schema preserved even with no rows
```

---

### Test 4.7: Full Dry Run Flow Through Phase 4
**Setup:** Valid config, both layers have data with `notes` field

**Steps:**
1. Run: `python conflate.py --layer "FullFlowTest"` (dry run)
2. Capture all output

**Expected output sequence:**
```
Mode: DRY RUN — No changes will be written
Layer: FullFlowTest
Captured layer: <captured_name>
Authoritative layer: <auth_name>
Loading layers...
Loaded <n> captured features
Loaded <n> authoritative features
Validating schema...
Schema validation passed: notes_max_length=<value>
Creating backup...
Backup created: backup/FullFlowTest_backup_<YYYYMMDD_HHMMSS>.gpkg
Phase 4 complete. Ready for matching (Phase 5).
```

**Verification:**
- All lines present in correct order
- Feature counts are non-zero and match AGOL
- `notes_max_length` is a positive integer (or `None` for non-text notes)
- Backup file created with correct naming pattern

---

### Test 4.8: Multiple Runs — Timestamp Uniqueness (moved from 2.20)
**Setup:** Valid config, clean `backup/` directory

**Steps:**
1. Run: `python conflate.py --layer "TimestampTest"` (dry run)
2. Wait 2 seconds
3. Run again: `python conflate.py --layer "TimestampTest"` (dry run)
4. List backup files: `Get-ChildItem backup\TimestampTest_backup_*`

**Expected:**
- Two backup files with different timestamps
- Both files are valid GeoPackages with identical data
- Timestamps differ by at least 1 second

**Verification:**
```powershell
$files = Get-ChildItem backup\TimestampTest_backup_*
$files.Count  # should be 2
$files[0].Name  # e.g., TimestampTest_backup_20260714_143022.gpkg
$files[1].Name  # e.g., TimestampTest_backup_20260714_143024.gpkg
```

---

### Test 4.9: Non-Text Notes Field
**Setup:** Valid config, authoritative layer where `notes` field is a non-text type (e.g., Integer)

**Steps:**
1. Run: `python conflate.py --layer "NonTextNotesLayer"` (dry run)

**Expected:**
- Schema validation passes
- Output: `"Schema validation passed: notes_max_length=None"`
- Backup is created successfully

---

### Test 4.10: Restore Mode — Early Exit
**Setup:** Valid config, accessible layers

**Steps:**
1. Run: `python conflate.py --layer "RestoreTest" --restore`

**Expected:**
- Output: `"Mode: RESTORE — Will restore from backup"`
- Script authenticates to AGOL
- Script exits after Phase 4 (restore is Phase 10, not yet implemented)
- No data is loaded or backed up in restore mode
- Note: This test documents current behavior; restore will be complete in Phase 10

---

### Phase 4 (Schema Validation & Backup)
| # | Test | Purpose | Requires |
|---|------|---------|----------|
| 1 | 4.2 | Missing notes field (quick fail) | Layer without notes |
| 2 | 4.6 | Empty layer backup | Empty layer |
| 3 | 4.1 | **Happy path** (full flow) | Valid layer with notes |
| 4 | 4.3 | Backup file verification | From Test 4.1 |
| 5 | 4.4 | Backup dir auto-creation | From Test 4.1 |
| 6 | 4.5 | Checkpoint file I/O | None |
| 7 | 4.7 | Full dry run flow | From Test 4.1 |
| 8 | 4.8 | Timestamp uniqueness | From Test 4.7 |
| 9 | 4.9 | Non-text notes field | Layer with non-text notes |
| 10 | 4.10 | Restore mode early exit | Valid config |

---

## Part E: Phase 5 — Spatial Indexing & Matching (`conflate.py`)

### Prerequisites for Phase 5 tests
- `config.local.json` must exist with valid credentials and layer URLs
- `config.json` must exist with valid JSON (matching thresholds and paths)
- Layers referenced in config must have features with valid geometries
- Python environment must have `geopandas`, `pyproj` installed
- Phase 4 tests must pass (schema validation and backup working)

---

### Test 5.1: Clean Match — d2 Beyond Threshold
**Setup:** Valid config, both layers with data where some captured points are near one auth point and far from others

**Steps:**
1. Run: `python conflate.py --layer "CleanMatchTest"` (dry run)
2. Verify console output shows clean matches with d1 within threshold and d2 beyond threshold

**Expected output:**
```
Building spatial index...
Matching captured points to authoritative points...
INFO: Matched OBJECTID <n>: clean (d1=<x>.<y> ft, d2=<x>.<y> ft)
Matching complete: <n> clean, 0 ambiguous, 0 new
```

**Verification:**
- At least one "clean" match logged
- d1 value is less than `threshold_ft` (9 ft by default)
- d2 value is greater than `threshold_ft`

---

### Test 5.2: Clean Match — d2 Significantly Farther
**Setup:** Valid config, both layers with data where two auth points are close together but captured point is much closer to one

**Steps:**
1. Run: `python conflate.py --layer "CleanFartherTest"` (dry run)
2. Verify console output shows clean matches where d2 is within threshold but significantly farther than d1

**Expected output:**
```
INFO: Matched OBJECTID <n>: clean (d1=<x>.<y> ft, d2=<x>.<y> ft)
```

**Verification:**
- Match type is "clean" (not "ambiguous")
- d2 > d1 × 1.2 (where 1.2 = 1 + ambiguity_pct/100)

---

### Test 5.3: Ambiguous Match
**Setup:** Valid config, both layers with data where two auth points are very close together and a captured point is between them

**Steps:**
1. Run: `python conflate.py --layer "AmbiguousTest"` (dry run)
2. Verify console output shows ambiguous matches

**Expected output:**
```
INFO: Matched OBJECTID <n>: ambiguous (d1=<x>.<y> ft, d2=<x>.<y> ft)
```

**Verification:**
- Match type is "ambiguous"
- d1 and d2 are both within threshold
- d2 ≤ d1 × 1.2 (ambiguity factor)

---

### Test 5.4: New Match — d1 Beyond Threshold
**Setup:** Valid config, captured layer has points far from all auth points

**Steps:**
1. Run: `python conflate.py --layer "NewMatchTest"` (dry run)
2. Verify console output shows "new" matches

**Expected output:**
```
INFO: New OBJECTID <n>: no match within 9 ft (nearest: <x>.<y> ft)
```

**Verification:**
- Match type is "new"
- d1 value is greater than or equal to `threshold_ft` (9 ft)

---

### Test 5.5: Exact Threshold Boundary
**Setup:** Valid config, captured point positioned exactly at threshold distance from nearest auth point

**Steps:**
1. Run: `python conflate.py --layer "ThresholdTest"` (dry run)
2. Verify console output shows "new" match at exact threshold

**Expected output:**
```
INFO: New OBJECTID <n>: no match within 9 ft (nearest: 9.0 ft)
```

**Verification:**
- Match type is "new" (threshold is exclusive)
- d1 value equals `threshold_ft`

---

### Test 5.6: Empty Authoritative Layer
**Setup:** Valid config where authoritative layer is empty (0 features) but captured layer has data

**Steps:**
1. Run: `python conflate.py --layer "EmptyAuthTest"` (dry run)
2. Verify all captured points classified as "new"

**Expected output:**
```
INFO: New OBJECTID <n>: no match within 9 ft (nearest: N/A)
```

**Verification:**
- All match results have `match_type = "new"`
- `d1` and `d2` are `None` for all results

---

### Test 5.7: Single Authoritative Point
**Setup:** Valid config, authoritative layer has exactly one feature, captured layer has features near it

**Steps:**
1. Run: `python conflate.py --layer "SingleAuthTest"` (dry run)
2. Verify single auth point produces "clean" matches when within threshold

**Expected output:**
```
INFO: Matched OBJECTID <n>: clean (d1=<x>.<y> ft, d2=inf ft)
```

**Verification:**
- Match type is "clean" when within threshold
- `d2` is `None` (infinity converted to None)

---

### Test 5.8: Mixed Results
**Setup:** Valid config, both layers with multiple features at varying distances

**Steps:**
1. Run: `python conflate.py --layer "MixedTest"` (dry run)
2. Verify all three match types appear in output

**Expected output:**
```
INFO: Matched OBJECTID <n>: clean (d1=<x>.<y> ft, d2=<x>.<y> ft)
INFO: Matched OBJECTID <m>: ambiguous (d1=<x>.<y> ft, d2=<x>.<y> ft)
INFO: New OBJECTID <p>: no match within 9 ft (nearest: <x>.<y> ft)
Matching complete: <c> clean, <a> ambiguous, <n> new
```

**Verification:**
- All three match types (clean, ambiguous, new) appear
- Summary counts match the number of each type logged

---

### Test 5.9: Full Flow Through Phase 5
**Setup:** Valid config, both layers have data with `notes` field

**Steps:**
1. Run: `python conflate.py --layer "FullFlowPhase5"` (dry run)
2. Capture all output from Phase 4 through Phase 5

**Expected output sequence:**
```
Mode: DRY RUN — No changes will be written
Layer: FullFlowPhase5
Captured layer: <captured_name>
Authoritative layer: <auth_name>
Loading layers...
Loaded <n> captured features
Loaded <n> authoritative features
Validating schema...
Schema validation passed: notes_max_length=<value>
Creating backup...
Backup created: backup/FullFlowPhase5_backup_<YYYYMMDD_HHMMSS>.gpkg
Phase 4 complete. Ready for matching (Phase 5).
Building spatial index...
Matching captured points to authoritative points...
INFO: Matched OBJECTID <n>: <type> (d1=<x>.<y> ft, d2=<x>.<y> ft)
...
Matching complete: <c> clean, <a> ambiguous, <n> new
```

**Verification:**
- All Phase 4 lines present in correct order
- Phase 5 header lines present
- Per-point match logs present
- Summary line present with correct counts

---

### Test 5.10: Custom threshold_ft
**Setup:** Modify `config.json` to use a different threshold value

**Steps:**
1. Modify `config.json`:
   ```json
   {
     "matching": { "threshold_ft": 30, "ambiguity_pct": 20 },
     "paths": { "backup": "backup/", "reports": "reports/" }
   }
   ```
2. Run: `python conflate.py --layer "CustomThresholdTest"` (dry run)
3. Run with original config (threshold_ft=9) for comparison

**Expected:**
- With threshold_ft=30: more "clean" matches, fewer "new" matches
- With threshold_ft=9: fewer "clean" matches, more "new" matches
- Summary counts change between the two runs

**Verification:**
- The `threshold_ft` value from config is used in log messages
- Classification results change based on threshold value

---

### Test 5.11: Custom ambiguity_pct
**Setup:** Modify `config.json` to use a different ambiguity percentage

**Steps:**
1. Modify `config.json`:
   ```json
   {
     "matching": { "threshold_ft": 9, "ambiguity_pct": 50 },
     "paths": { "backup": "backup/", "reports": "reports/" }
   }
   ```
2. Run: `python conflate.py --layer "CustomAmbiguityTest"` (dry run)
3. Run with original config (ambiguity_pct=20) for comparison

**Expected:**
- With ambiguity_pct=50: fewer "ambiguous" matches (wider clean zone)
- With ambiguity_pct=20: more "ambiguous" matches (narrower clean zone)
- Summary counts change between the two runs

**Verification:**
- Classification results change based on ambiguity_pct value
- The ambiguity factor (1 + ambiguity_pct/100) is correctly applied

---

### Phase 5 (Spatial Indexing & Matching)
| # | Test | Purpose | Requires |
|---|------|---------|----------|
| 1 | 5.6 | Empty auth layer (quick fail) | Empty auth layer |
| 2 | 5.4 | New match (d1 beyond threshold) | Layer with distant points |
| 3 | 5.5 | Exact threshold boundary | Layer with points at 9ft |
| 4 | 5.1 | **Clean match — d2 beyond** | Two auth points, captured near one |
| 5 | 5.2 | Clean match — d2 significantly farther | Two close auth points |
| 6 | 5.3 | **Ambiguous match** | Two very close auth points |
| 7 | 5.7 | Single auth point | One auth point |
| 8 | 5.8 | **Mixed results** | Both layers with multiple features |
| 9 | 5.9 | **Full flow** | Valid config, both layers with data |
| 10 | 5.10 | Custom threshold_ft | Modify config.json |
| 11 | 5.11 | Custom ambiguity_pct | Modify config.json |

---

## Cleanup

After all tests pass:

```powershell
# Remove test config (contains real credentials)
Remove-Item config.local.json -Force

# Remove test backups and reports
Remove-Item backup\* -Recurse -Force
Remove-Item reports\* -Force

# Remove any test checkpoint files
Remove-Item backup\*checkpoint*.json -Force

# Restore config.json if modified
# (revert custom paths back to defaults)
```

---

## Quick Reference: Input Cheat Sheet

### Phase 1

| Test | Username | Password | URL 1 | URL 2 | Extra Inputs |
|------|----------|----------|-------|-------|--------------|
| 1.4 (Bad creds) | wrong | wrong | — | — | — |
| 1.8 (Empty URL) | valid | valid | `` (empty) | valid | Enter, Enter |
| 1.1 (Happy) | valid | valid | valid | valid | — |
| 1.2 (Overwrite-) | N/A | N/A | N/A | N/A | Enter (at overwrite) |
| 1.3 (Overwrite+) | valid | valid | valid | valid | `y` (at overwrite) |
| 1.5 (Both invalid) | valid | valid | fake | fake | `y`, `n` (retries) |
| 1.6 (One invalid) | valid | valid | valid | fake | `y`, `n` (retries) |
| 1.7 (Wrong type) | valid | valid | wrong-type | valid | `y` (retry) |

### Phase 2

| Test | Config State | Flags |
|------|-------------|-------|
| 2.1 | Valid | (omit --layer) |
| 2.2 | Any | `--help` |
| 2.3–2.6, 2.14, 2.18–2.20 | Valid | `--layer "X"` + various |
| 2.7–2.10 | Missing/invalid config | `--layer "X"` |
| 2.11–2.13 | Bad/missing creds | `--layer "X"` |
| 2.15–2.16 | Modified config.json | `--layer "X"` |
| 2.17 | Superseded | See Phase 3 |

### Phase 3

| Test | Layer State | What to Check |
|------|------------|---------------|
| 3.1 | Valid data | GeoDataFrame structure, fields, CRS |
| 3.2 | Has null geoms | Skip warnings in logs |
| 3.3 | Empty layer | Graceful empty GDF |
| 3.4 | Northern location | EPSG 326xx in logs |
| 3.5 | Southern location | EPSG 327xx in logs |
| 3.6 | Any data | Meter-scale coordinates |
| 3.7 | Two valid layers | Full pipeline, WGS84 + UTM |
| 3.8 | Empty auth | Fallback UTM detection |
| 3.9 | Both empty | Default EPSG:32618 |

### Phase 4

| Test | Layer State | What to Check |
|------|------------|---------------|
| 4.1 | Valid data with notes | Full output sequence, backup created |
| 4.2 | Missing notes field | FATAL error, exit code 1 |
| 4.3 | Valid data | Backup GPKG readable, all fields present |
| 4.4 | Valid data, no backup/ dir | Directory auto-created |
| 4.5 | N/A (manual checkpoint) | JSON I/O works correctly |
| 4.6 | Empty layer | Backup created, 0 rows |
| 4.7 | Both layers with data | Complete output sequence |
| 4.8 | Valid config | Two unique backup timestamps |
| 4.9 | Non-text notes | notes_max_length=None |
| 4.10 | Valid config | Restore exits early (Phase 10 pending) |
