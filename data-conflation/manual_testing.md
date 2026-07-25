# Manual Test Suite — Phases 1–6 ✅ **COMPLETE**

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

## Part F: Phase 6 — Global Collision Resolution (`conflate.py`) ✅ **DONE**

### Prerequisites for Phase 6 tests
- `config.local.json` must exist with valid credentials and layer URLs
- `config.json` must exist with valid JSON (matching thresholds and paths)
- Layers referenced in config must have features with valid geometries
- Python environment must have `geopandas`, `pyproj` installed
- Phase 5 tests must pass (spatial indexing and matching working)
- Need at least one case where multiple captured points match the same auth point

---

### Test 6.1: Collision Detected and Resolved (Happy Path)
**Setup:** Valid config, both layers with data where at least two captured points match the same auth point within threshold

**Steps:**
1. Run: `python conflate.py --layer "CollisionTest"` (dry run)
2. Verify console output shows collision detection and resolution

**Expected output:**
```
Building spatial index...
Matching captured points to authoritative points...
INFO: Matched OBJECTID <n>: clean (d1=<x>.<y> ft, d2=<x>.<y> ft)
...
INFO: Collision detected: auth GlobalID <gid> claimed by captured OBJECTID <oid1> (d=<d1> ft) and captured OBJECTID <oid2> (d=<d2> ft)
  -> OBJECTID <closest_oid> retains match (closest)
  -> OBJECTID <farther_oid> reclassified as new
Detecting collisions...
Resolving <n> collision(s)...
After collision resolution: <c> clean, <a> ambiguous, <n> new
```

**Verification:**
- Collision log message present with correct GlobalID
- Closest captured point retains its match
- Farther captured point reclassified as "new"
- Post-resolution summary shows updated counts (one fewer clean/ambiguous, one more new)

---

### Test 6.2: No Collisions Detected
**Setup:** Valid config, both layers with data where each captured point matches a unique auth point (one-to-one mapping)

**Steps:**
1. Run: `python conflate.py --layer "NoCollisionTest"` (dry run)
2. Verify console output shows no collisions

**Expected output:**
```
Matching complete: <n> clean, <a> ambiguous, <m> new
Detecting collisions...
No collisions detected.
```

**Verification:**
- "No collisions detected." message present
- No collision resolution messages
- Summary unchanged from Phase 5

---

### Test 6.3: Multiple Separate Collisions
**Setup:** Valid config, data where multiple distinct auth points are each claimed by multiple captured points

**Steps:**
1. Run: `python conflate.py --layer "MultipleCollisionsTest"` (dry run)
2. Verify all collisions are detected and resolved

**Expected output:**
```
INFO: Collision detected: auth GlobalID <gid1> claimed by captured OBJECTID <...> and captured OBJECTID <...>
  -> OBJECTID <winner1> retains match (closest)
  -> OBJECTID <loser1> reclassified as new
INFO: Collision detected: auth GlobalID <gid2> claimed by captured OBJECTID <...> and captured OBJECTID <...>
  -> OBJECTID <winner2> retains match (closest)
  -> OBJECTID <loser2> reclassified as new
Resolving 2 collision(s)...
```

**Verification:**
- Each collision logged separately with its own GlobalID
- Each winner retains match, each loser reclassified
- Total collision count matches number of logged collisions

---

### Test 6.4: Tie-Breaking by Lower OBJECTID
**Setup:** Valid config, two captured points at approximately the same distance from the same auth point

**Steps:**
1. Run: `python conflate.py --layer "TieBreakTest"` (dry run)
2. Verify the lower OBJECTID wins the tie

**Expected output:**
```
INFO: Collision detected: auth GlobalID <gid> claimed by captured OBJECTID <low_oid> (d=<d> ft) and captured OBJECTID <high_oid> (d=<d> ft)
  -> OBJECTID <low_oid> retains match (closest)
  -> OBJECTID <high_oid> reclassified as new
```

**Verification:**
- Lower OBJECTID retains the match despite equal distances
- Higher OBJECTID reclassified as new

---

### Test 6.5: Three or More Claimants
**Setup:** Valid config, data where three or more captured points match the same auth point

**Steps:**
1. Run: `python conflate.py --layer "ThreeClaimantsTest"` (dry run)
2. Verify closest wins and all others reclassified

**Expected output:**
```
INFO: Collision detected: auth GlobalID <gid> claimed by captured OBJECTID <oid1> (d=<d1> ft), captured OBJECTID <oid2> (d=<d2> ft), and captured OBJECTID <oid3> (d=<d3> ft)
  -> OBJECTID <closest_oid> retains match (closest)
  -> OBJECTID <loser1> reclassified as new
  -> OBJECTID <loser2> reclassified as new
```

**Verification:**
- Closest captured point retains match
- Both other captured points reclassified as "new"
- Only one winner per collision group

---

### Test 6.6: All-New Results (No Collisions Possible)
**Setup:** Valid config, authoritative layer is empty or all captured points are beyond threshold

**Steps:**
1. Run: `python conflate.py --layer "AllNewTest"` (dry run)
2. Verify no collision detection errors

**Expected output:**
```
Matching complete: 0 clean, 0 ambiguous, <n> new
Detecting collisions...
No collisions detected.
```

**Verification:**
- No collision detection errors
- All captured points remain "new"
- "No collisions detected." message present

---

### Test 6.7: Full Flow Through Phase 6
**Setup:** Valid config, both layers with data including at least one collision scenario

**Steps:**
1. Run: `python conflate.py --layer "FullFlowPhase6"` (dry run)
2. Capture all output from Phase 4 through Phase 6

**Expected output sequence:**
```
Mode: DRY RUN — No changes will be written
Layer: FullFlowPhase6
Captured layer: <captured_name>
Authoritative layer: <auth_name>
Loading layers...
Loaded <n> captured features
Loaded <n> authoritative features
Validating schema...
Schema validation passed: notes_max_length=<value>
Creating backup...
Backup created: backup/FullFlowPhase6_backup_<YYYYMMDD_HHMMSS>.gpkg
Phase 4 complete. Ready for matching (Phase 5).
Building spatial index...
Matching captured points to authoritative points...
INFO: Matched OBJECTID <n>: <type> (d1=<x>.<y> ft, d2=<x>.<y> ft)
...
Matching complete: <c> clean, <a> ambiguous, <n> new
Detecting collisions...
INFO: Collision detected: auth GlobalID <gid> claimed by captured OBJECTID <...> and captured OBJECTID <...>
  -> OBJECTID <winner> retains match (closest)
  -> OBJECTID <loser> reclassified as new
Resolving 1 collision(s)...
After collision resolution: <c'> clean, <a'> ambiguous, <n'> new
```

**Verification:**
- All Phase 4 lines present in correct order
- Phase 5 matching and summary present
- Phase 6 collision detection and resolution present
- Post-resolution summary shows updated counts (losers moved from clean/ambiguous to new)

---

### Test 6.8: Logging Output Verification
**Setup:** Valid config, data with known collision scenario

**Steps:**
1. Run: `python conflate.py --layer "LoggingTest"` (dry run)
2. Capture console output
3. Verify collision log format

**Expected log format:**
```
Collision detected: auth GlobalID <globalid> claimed by captured OBJECTID <oid1> (d=<d1> ft) and captured OBJECTID <oid2> (d=<d2> ft)
  -> OBJECTID <winner_oid> retains match (closest)
  -> OBJECTID <loser_oid> reclassified as new
```

**Verification:**
- Collision message includes GlobalID of claimed auth point
- All claimants listed with distances
- Winner clearly identified as "retains match (closest)"
- Losers clearly identified as "reclassified as new"
- Arrow format (`->`) used consistently

---

### Test 6.9: Post-Resolution Match Consistency
**Setup:** Valid config, data with collision scenario

**Steps:**
1. Run: `python conflate.py --layer "ConsistencyTest"` (dry run)
2. Verify that reclassified "new" records have consistent fields

**Verification (via inspection of code/logic):**
- All reclassified "new" records have `auth_globalid = None`
- All reclassified "new" records have `auth_objectid = None`
- All reclassified "new" records have `auth_geom_wgs84 = None`
- All reclassified "new" records have `distance_ft = None`
- Winners retain all their original match fields

---

### Phase 6 (Global Collision Resolution)
| # | Test | Purpose | Requires |
|---|------|---------|----------|
| 1 | 6.2 | No collisions (quick pass) | One-to-one matching |
| 2 | 6.6 | All-new results | Empty auth or all distant |
| 3 | 6.1 | **Collision detected and resolved** | Multi-captured, same auth |
| 4 | 6.3 | Multiple separate collisions | Multiple collision groups |
| 5 | 6.4 | Tie-breaking by OBJECTID | Equal-distance claimants |
| 6 | 6.5 | Three or more claimants | 3+ captured, same auth |
| 7 | 6.7 | **Full flow** | Valid config, collision scenario |
| 8 | 6.8 | Logging output verification | Collision scenario |
| 9 | 6.9 | Post-resolution consistency | Collision scenario |

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

### Phase 6

| Test | Layer State | What to Check |
|------|------------|---------------|
| 6.1 | Collision scenario | Collision logged, winner retains, loser reclassified |
| 6.2 | One-to-one matching | "No collisions detected." |
| 6.3 | Multiple collision groups | All collisions resolved |
| 6.4 | Equal-distance claimants | Lower OBJECTID wins |
| 6.5 | 3+ claimants | Closest wins, all others reclassified |
| 6.6 | All-new results | No collision errors |
| 6.7 | Full flow with collision | Complete output sequence |
| 6.8 | Collision scenario | Log format correct |
| 6.9 | Collision scenario | Reclassified records consistent |

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

---

## Part G: Phase 7 — Dry Run Output (`conflate.py`)

**Status:** COMPLETE ✅

### Prerequisites for Phase 7 tests
- `config.local.json` must exist with valid credentials and layer URLs
- `config.json` must exist with valid JSON (matching thresholds and paths)
- Layers referenced in config must have features with valid geometries
- Python environment must have `geopandas`, `pyproj`, `requests` installed
- Phase 6 tests must pass (collision resolution working)

---

### Test 7.1: Dry Run — Review GeoPackage Created

**Setup:** Valid config, both layers with data

**Steps:**
1. Run: `python conflate.py --layer "ReviewTest"` (dry run)

**Expected output:**
```
Mode: DRY RUN — No changes will be written
Layer: ReviewTest
Captured layer: <captured_name>
Authoritative layer: <auth_name>
Loading layers...
Loaded <n> captured features
Loaded <n> authoritative features
Validating schema...
Schema validation passed: notes_max_length=<value>
Creating backup...
Backup created: backup/ReviewTest_backup_<timestamp>.gpkg
Phase 4 complete. Ready for matching (Phase 5).
Building spatial index...
Matching captured points to authoritative points...
INFO: Matched OBJECTID <n>: <type> (d1=<x>.<y> ft, d2=<x>.<y> ft)
Matching complete: <c> clean, <a> ambiguous, <n> new
Detecting collisions...
No collisions detected. (or: Resolving <n> collision(s)...)
After collision resolution: <c'> clean, <a'> ambiguous, <n'> new
Writing review file...
Review file created: backup/ReviewTest_conflation_review.gpkg
Writing CSV report...
Report written: reports/ReviewTest_<timestamp>.csv
=== Conflation Summary ===
Matched (clean):     <c'>
Matched (ambiguous): <a'>
New:                 <n'>
Attachments pending: <count>
Total:               <total>
```

**Verification:**
```powershell
# Check review file exists
Test-Path "backup\ReviewTest_conflation_review.gpkg"  # should be True

# Check CSV report exists
Test-Path "reports\ReviewTest_*.csv"  # should be True

# Verify GeoPackage has 4 tables
sqlite3 "backup\ReviewTest_conflation_review.gpkg" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
# Should output: current_state, proposed_attachments, proposed_new, proposed_updates
```

---

### Test 7.2: Review GeoPackage — `current_state` Table

**Setup:** Valid config, authoritative layer with known data

**Steps:**
1. Run dry run with the layer
2. Read the `current_state` table from the review GeoPackage

**Verification:**
```python
import geopandas as gpd
gdf = gpd.read_file("backup/ReviewTest_conflation_review.gpkg", layer="current_state")
# Should match the authoritative layer exactly
assert len(gdf) == <auth_feature_count>
assert "OBJECTID" in gdf.columns
assert "GlobalID" in gdf.columns
assert "COMMENTNOTES" in gdf.columns
assert gdf.crs.to_epsg() == 4326
```

---

### Test 7.3: Review GeoPackage — `proposed_updates` Table

**Setup:** Valid config, both layers with data where some captured points match auth points

**Steps:**
1. Run dry run with the layer
2. Read the `proposed_updates` table

**Verification:**
```python
import geopandas as gpd
gdf = gpd.read_file("backup/ReviewTest_conflation_review.gpkg", layer="proposed_updates")
# Should have dual geometry columns
assert "old_geometry" in gdf.columns
assert "new_geometry" in gdf.columns
# Should have metadata columns
assert "match_type" in gdf.columns
assert "action" in gdf.columns
assert "distance_ft" in gdf.columns
assert "captured_objectid" in gdf.columns
assert "label" in gdf.columns
# All rows should have action="updated"
assert all(gdf["action"] == "updated")
# All rows should have match_type in (clean, ambiguous)
assert all(gdf["match_type"].isin(["clean", "ambiguous"]))
```

---

### Test 7.4: Review GeoPackage — `proposed_new` Table

**Setup:** Valid config, captured layer has points that don't match any auth point

**Steps:**
1. Run dry run with the layer
2. Read the `proposed_new` table
3. Verify notes concatenation

**Verification:**
```python
import geopandas as gpd
gdf = gpd.read_file("backup/ReviewTest_conflation_review.gpkg", layer="proposed_new")
# Should have metadata columns
assert "match_type" in gdf.columns
assert "action" in gdf.columns
assert "captured_objectid" in gdf.columns
assert "label" in gdf.columns
# All rows should have action="appended"
assert all(gdf["action"] == "appended")
# All rows should have match_type="new"
assert all(gdf["match_type"] == "new")
# Label should contain threshold info
assert all(gdf["label"].str.contains("no match within"))
```

---

### Test 7.5: Review GeoPackage — `proposed_attachments` Table

**Setup:** Valid config, captured layer has records with attachments

**Steps:**
1. Run dry run with the layer
2. Read the `proposed_attachments` table

**Verification:**
```python
import geopandas as gpd
gdf = gpd.read_file("backup/ReviewTest_conflation_review.gpkg", layer="proposed_attachments")
# Should have attachment metadata columns
assert "captured_objectid" in gdf.columns
assert "auth_globalid" in gdf.columns
assert "attachment_name" in gdf.columns
assert "attachment_size_bytes" in gdf.columns
assert "attachment_type" in gdf.columns
assert "status" in gdf.columns
# All rows should have status="pending"
assert all(gdf["status"] == "pending")
```

---

### Test 7.6: CSV Report Verification

**Setup:** Valid config, both layers with data

**Steps:**
1. Run dry run with the layer
2. Read the CSV report

**Verification:**
```python
import pandas as pd
df = pd.read_csv("reports/ReviewTest_<timestamp>.csv")
# Should have correct columns
expected_cols = ["layer", "captured_objectid", "auth_globalid", "distance_ft", "match_type", "action", "attachment_count", "attachment_names"]
assert list(df.columns) == expected_cols
# Should have one row per captured record
assert len(df) == <captured_feature_count>
# Matched records should have action="updated"
updated = df[df["action"] == "updated"]
assert len(updated) == <clean + ambiguous count>
# New records should have action="appended"
appended = df[df["action"] == "appended"]
assert len(appended) == <new count>
```

---

### Test 7.7: Summary Output Verification

**Setup:** Valid config, both layers with data

**Steps:**
1. Run dry run with the layer
2. Capture console output

**Verification:**
- Output contains `=== Conflation Summary ===`
- `Matched (clean):` count matches actual clean matches
- `Matched (ambiguous):` count matches actual ambiguous matches
- `New:` count matches actual new matches
- `Attachments pending:` shows attachment count
- `Total:` equals sum of clean + ambiguous + new

---

### Test 7.8: `--auto-open` Flag

**Setup:** Valid config, both layers with data

**Steps:**
1. Run: `python conflate.py --layer "AutoOpenTest" --auto-open`

**Expected:**
- Full dry run flow completes
- Review GeoPackage is created
- On Windows: review file opens automatically
- On Linux: xdg-open is invoked

**Verification:**
- Review file exists on disk
- No errors related to auto-open (file may or may not actually open in CI)

---

### Test 7.9: Empty Results — All Tables Created

**Setup:** Valid config, both layers with data but no matches (e.g., very small threshold or far-apart points)

**Steps:**
1. Run dry run with the layer
2. Verify all 4 tables exist in the review GeoPackage

**Verification:**
```powershell
sqlite3 "backup\EmptyTest_conflation_review.gpkg" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
# Should output: current_state, proposed_attachments, proposed_new, proposed_updates
# All tables exist even if empty
```

---

### Test 7.10: Full Flow Through Phase 7

**Setup:** Valid config, both layers with data including clean matches, ambiguous matches, new records, and at least one collision

**Steps:**
1. Run: `python conflate.py --layer "FullFlowPhase7"` (dry run)
2. Capture all output
3. Verify review GeoPackage and CSV report

**Expected output sequence:**
```
Mode: DRY RUN — No changes will be written
Layer: FullFlowPhase7
Captured layer: <captured_name>
Authoritative layer: <auth_name>
Loading layers...
Loaded <n> captured features
Loaded <n> authoritative features
Validating schema...
Schema validation passed: notes_max_length=<value>
Creating backup...
Backup created: backup/FullFlowPhase7_backup_<timestamp>.gpkg
Phase 4 complete. Ready for matching (Phase 5).
Building spatial index...
Matching captured points to authoritative points...
INFO: Matched OBJECTID <n>: <type> (d1=<x>.<y> ft, d2=<x>.<y> ft)
...
Matching complete: <c> clean, <a> ambiguous, <n> new
Detecting collisions...
INFO: Collision detected: auth GlobalID <gid> claimed by captured OBJECTID <...> and captured OBJECTID <...>
  -> OBJECTID <winner> retains match (closest)
  -> OBJECTID <loser> reclassified as new
Resolving 1 collision(s)...
After collision resolution: <c'> clean, <a'> ambiguous, <n'> new
Writing review file...
Review file created: backup/FullFlowPhase7_conflation_review.gpkg
Writing CSV report...
Report written: reports/FullFlowPhase7_<timestamp>.csv
=== Conflation Summary ===
Matched (clean):     <c'>
Matched (ambiguous): <a'>
New:                 <n'>
Attachments pending: <count>
Total:               <total>
```

**Verification:**
- All Phase 4-6 lines present in correct order
- Phase 7 output lines present
- Review GeoPackage has 4 tables with correct data
- CSV report has correct columns and row counts
- Summary counts match actual data

---

### Phase 7 (Dry Run Output)

| # | Test | Purpose | Requires |
|---|------|---------|----------|
| 1 | 7.9 | Empty results — all tables exist | Data with no matches |
| 2 | 7.1 | **Happy path** (full dry run) | Valid config, both layers with data |
| 3 | 7.2 | current_state table verification | From Test 7.1 |
| 4 | 7.3 | proposed_updates table verification | From Test 7.1 |
| 5 | 7.4 | proposed_new table with notes | From Test 7.1 |
| 6 | 7.5 | proposed_attachments table | Captured records with attachments |
| 7 | 7.6 | CSV report verification | From Test 7.1 |
| 8 | 7.7 | Summary output verification | From Test 7.1 |
| 9 | 7.8 | --auto-open flag | From Test 7.1 |
| 10 | 7.10 | **Full flow** | Valid config, collision scenario |

---

## Part H: Phase 8 — Apply Changes (`conflate.py`)

### Prerequisites for Phase 8 tests
- `config.local.json` must exist with valid credentials and layer URLs
- `config.json` must exist with valid JSON (matching thresholds, paths, and `apply` section)
- Layers referenced in config must have features with valid geometries and a `COMMENTNOTES` field
- Python environment must have `geopandas`, `pyproj` installed
- Phase 7 tests must pass (dry run output working)
- **Important:** Phase 8 writes live data to AGOL — use a test layer, not production

---

### Test 8.1: Apply — Happy Path (Full Flow)

**Setup:** Valid config, test layer with known data (use a non-production layer)

**Steps:**
1. Record the current state of the authoritative layer (feature count, some attribute values)
2. Run: `python conflate.py --layer "ApplyTest" --apply --no-resume`
3. Verify the layer in AGOL was updated

**Expected output:**
```
Mode: APPLY — Changes will be written to AGOL
Layer: ApplyTest
Captured layer: <captured_name>
Authoritative layer: <auth_name>
Loading layers...
Loaded <n> captured features
Loaded <n> authoritative features
Validating schema...
Schema validation passed: notes_max_length=<value>
Creating backup...
Backup created: backup/ApplyTest_backup_<YYYYMMDD_HHMMSS>.gpkg
Phase 4 complete. Ready for matching (Phase 5).
Building spatial index...
Matching captured points to authoritative points...
INFO: Matched OBJECTID <n>: <type> (d1=<x>.<y> ft, d2=<x>.<y> ft)
...
Matching complete: <c> clean, <a> ambiguous, <n> new
Detecting collisions...
No collisions detected.
After collision resolution: <c'> clean, <a'> ambiguous, <n'> new
Writing review file...
Review file created: backup/ApplyTest_conflation_review.gpkg
Writing CSV report...
Report written: reports/ApplyTest_<timestamp>.csv
=== Conflation Summary ===
Matched (clean):     <c'>
Matched (ambiguous): <a'>
New:                 <n'>
Attachments pending: <count>
Total:               <total>

Phase 8: Applying changes to AGOL...
Previous checkpoint found. Starting fresh. Ignoring previous checkpoint.
Updating batch 1/1 (N records)...
  Batch 1: N updated successfully
Appending batch 1/1 (N records)...
  Batch 1: N appended successfully
Checkpoint deleted — all changes applied successfully
```

**Verification:**
```python
# Verify layer was updated in AGOL
from arcgis.gis import GIS
gis = GIS("https://www.arcgis.com", username, password)
layer = gis.content.get(layer_item_id)
fl = layer.layers[0]
# Feature count should have increased by number of "new" records
assert fl.properties.currentVersion == <expected_version>
```

---

### Test 8.2: Checkpoint — Resume from Previous Run

**Setup:** Valid config, checkpoint file exists from a partial run

**Steps:**
1. Create a checkpoint file manually (simulating a partial run):
```json
{
  "timestamp": "20260718_120000",
  "layer": "ResumeTest",
  "applied_updates": ["{gid1}", "{gid2}"],
  "applied_new": ["{gid3}"]
}
```
2. Run: `python conflate.py --layer "ResumeTest" --apply`
3. When prompted, press Enter (default = yes)

**Expected output:**
```
Previous checkpoint found. Resume from previous run? [Y/n]:
Resuming: 2 updates and 1 new records already applied
Updating batch 1/1 (N records)...  (only unapplied records)
  Batch 1: N updated successfully
  Batch 2: M appended successfully
Checkpoint deleted — all changes applied successfully
```

**Verification:**
- Only unapplied records are processed
- Checkpoint file is deleted after successful completion

---

### Test 8.3: Checkpoint — Ignore Previous Run (`--no-resume`)

**Setup:** Valid config, checkpoint file exists from a previous run

**Steps:**
1. Create a checkpoint file manually (as in Test 8.2)
2. Run: `python conflate.py --layer "NoResumeTest" --apply --no-resume`

**Expected output:**
```
Previous checkpoint found. Starting fresh. Ignoring previous checkpoint.
Updating batch 1/1 (N records)...
  Batch 1: N updated successfully
  Batch 2: M appended successfully
Checkpoint deleted — all changes applied successfully
```

**Verification:**
- All records are processed (not just unapplied ones)
- Old checkpoint is deleted
- New checkpoint is created and then deleted on success

---

### Test 8.4: Checkpoint — Auto-Resume (`--resume`)

**Setup:** Valid config, checkpoint file exists from a previous run

**Steps:**
1. Create a checkpoint file manually (as in Test 8.2)
2. Run: `python conflate.py --layer "AutoResumeTest" --apply --resume`

**Expected output:**
```
Previous checkpoint found. Resuming from previous run.
Resuming: 2 updates and 1 new records already applied
Updating batch 1/1 (N records)...
  Batch 1: N updated successfully
  Batch 2: M appended successfully
Checkpoint deleted — all changes applied successfully
```

**Verification:**
- No interactive prompt (auto-resumes)
- Only unapplied records are processed

---

### Test 8.5: Checkpoint — No Checkpoint with `--resume`

**Setup:** Valid config, no checkpoint file exists

**Steps:**
1. Ensure no checkpoint files exist in `backup/`
2. Run: `python conflate.py --layer "NoCheckpointResumeTest" --apply --resume`

**Expected:**
- Output: `"No checkpoint found at <path>. Cannot resume."`
- Script exits with code 1

---

### Test 8.6: Apply Updates — Null Preservation

**Setup:** Valid config, captured layer has records with some null attribute values

**Steps:**
1. Run: `python conflate.py --layer "NullPreserveTest" --apply --no-resume`
2. After apply, check the authoritative layer in AGOL

**Verification:**
- Records with null captured values retained their original auth values
- Non-null captured values were updated
- `COMMENTNOTES` field has appended notes (not overwritten)

**Verification (via AGOL inspection):**
```python
# Check a specific record that had null captured values
# The original auth value should be preserved
```

---

### Test 8.7: Apply Updates — Notes Appending

**Setup:** Valid config, authoritative layer records have existing `COMMENTNOTES` values

**Steps:**
1. Record the existing `COMMENTNOTES` values before apply
2. Run: `python conflate.py --layer "NotesAppendTest" --apply --no-resume`
3. Check the `COMMENTNOTES` values after apply

**Verification:**
- New notes are appended to existing notes (not overwritten)
- Format: `"Existing notes | FieldName: value | FieldName: value"`
- Notes are truncated to `notes_max_length` if needed

---

### Test 8.8: Apply Appends — New Records

**Setup:** Valid config, captured layer has records that don't match any auth record

**Steps:**
1. Run: `python conflate.py --layer "AppendTest" --apply --no-resume`
2. After apply, check the authoritative layer in AGOL

**Verification:**
- New records were added to the authoritative layer
- Feature count increased by the number of "new" records
- Notes field contains concatenated non-matching attributes
- `COMMENTNOTES` does NOT contain existing notes (there were none)

---

### Test 8.9: Batch Processing — Large Dataset

**Setup:** Valid config, layer with many records (enough to require multiple batches)

**Steps:**
1. Modify `config.json` to use a small batch size for testing:
```json
{
  "apply": { "batch_size": 5, "max_retries": 3 }
}
```
2. Run: `python conflate.py --layer "BatchTest" --apply --no-resume`

**Expected output:**
```
Phase 8: Applying changes to AGOL...
Updating batch 1/10 (5 records)...
  Batch 1: 5 updated successfully
Updating batch 2/10 (5 records)...
  Batch 2: 5 updated successfully
...
Updating batch 10/10 (5 records)...
  Batch 10: 5 updated successfully
Appending batch 1/5 (5 records)...
  Batch 1: 5 appended successfully
...
```

**Verification:**
- Batch progress messages show correct batch numbers
- All records processed
- Config `batch_size` is respected

---

### Test 8.10: Failed Record — Checkpoint Preservation

**Setup:** Valid config, layer where some updates will fail (e.g., invalid field value)

**Steps:**
1. Run: `python conflate.py --layer "FailTest" --apply --no-resume`
2. Some records should fail (e.g., due to invalid field values)

**Expected output:**
```
Phase 8: Applying changes to AGOL...
Updating batch 1/1 (N records)...
  Failed to update OBJECTID <oid> (GlobalID <gid>): <error>
  Batch 1 failed: <error>. Falling back to one-at-a-time...
  Failed to update OBJECTID <oid>: <error>
Checkpoint preserved at backup/FailTest_checkpoint_<timestamp>.json — <n> failures remain. Re-run to resume.
  Failed update: GlobalID <gid>
```

**Verification:**
- Checkpoint file is preserved (not deleted)
- Failed records are listed in the output
- Successfully applied records are in the checkpoint's `applied_updates`

---

### Test 8.11: Resume After Partial Failure

**Setup:** Valid config, checkpoint file exists from Test 8.10 (with failures)

**Steps:**
1. Fix the underlying issue that caused failures (if applicable)
2. Run: `python conflate.py --layer "FailTest" --apply --resume`

**Expected output:**
```
Previous checkpoint found. Resuming from previous run.
Resuming: <n> updates and <m> new records already applied
Updating batch 1/1 (N records)...  (only the failed records)
  Batch 1: N updated successfully
Checkpoint deleted — all changes applied successfully
```

**Verification:**
- Only failed records are retried
- Checkpoint is deleted after all records succeed
- No duplicate updates for previously successful records

---

### Test 8.12: Notes Truncation

**Setup:** Valid config, layer with very long notes that exceed `notes_max_length`

**Steps:**
1. Run: `python conflate.py --layer "TruncationTest" --apply --no-resume`

**Expected output:**
- Log warning: `"Notes truncated to <max> chars"`
- Notes field in AGOL is at most `notes_max_length` characters

**Verification:**
```python
# Check a record that had truncated notes
# Length should be <= notes_max_length
```

---

### Test 8.13: Full Flow Through Phase 8

**Setup:** Valid config, both layers with data including clean matches, ambiguous matches, new records, and at least one collision

**Steps:**
1. Run: `python conflate.py --layer "FullFlowPhase8" --apply --no-resume`
2. Capture all output
3. Verify AGOL layer was updated correctly

**Expected output sequence:**
```
Mode: APPLY — Changes will be written to AGOL
Layer: FullFlowPhase8
Captured layer: <captured_name>
Authoritative layer: <auth_name>
Loading layers...
Loaded <n> captured features
Loaded <n> authoritative features
Validating schema...
Schema validation passed: notes_max_length=<value>
Creating backup...
Backup created: backup/FullFlowPhase8_backup_<YYYYMMDD_HHMMSS>.gpkg
Phase 4 complete. Ready for matching (Phase 5).
Building spatial index...
Matching captured points to authoritative points...
INFO: Matched OBJECTID <n>: <type> (d1=<x>.<y> ft, d2=<x>.<y> ft)
...
Matching complete: <c> clean, <a> ambiguous, <n> new
Detecting collisions...
INFO: Collision detected: auth GlobalID <gid> claimed by captured OBJECTID <...> and captured OBJECTID <...>
  -> OBJECTID <winner> retains match (closest)
  -> OBJECTID <loser> reclassified as new
Resolving 1 collision(s)...
After collision resolution: <c'> clean, <a'> ambiguous, <n'> new
Writing review file...
Review file created: backup/FullFlowPhase8_conflation_review.gpkg
Writing CSV report...
Report written: reports/FullFlowPhase8_<timestamp>.csv
=== Conflation Summary ===
Matched (clean):     <c'>
Matched (ambiguous): <a'>
New:                 <n'>
Attachments pending: <count>
Total:               <total>

Phase 8: Applying changes to AGOL...
Previous checkpoint found. Starting fresh. Ignoring previous checkpoint.
Updating batch 1/1 (<c'+<a'> records)...
  Batch 1: <c'+<a'> updated successfully
Appending batch 1/1 (<n'> records)...
  Batch 1: <n'> appended successfully
Checkpoint deleted — all changes applied successfully
```

**Verification:**
- All Phase 4-7 lines present in correct order
- Phase 8 output lines present
- AGOL layer updated: matched records have new geometry/attributes, new records added
- Checkpoint deleted (all success)

---

### Test 8.14: Batch Failure — One-at-a-Time Fallback

**Setup:** Valid config, layer where a batch of updates will fail but individual records might succeed

**Steps:**
1. Run: `python conflate.py --layer "BatchFailTest" --apply --no-resume`

**Expected output:**
```
Updating batch 1/1 (N records)...
  Batch 1 failed: <error>. Falling back to one-at-a-time...
  Updated OBJECTID <oid> (GlobalID <gid>) — <field_count> fields changed
  Failed to update OBJECTID <oid> (GlobalID <gid>): <error>
```

**Verification:**
- Batch failure triggers one-at-a-time fallback
- Some records succeed individually while others fail
- Checkpoint reflects partial success

---

### Phase 8 (Apply Changes)

| # | Test | Purpose | Requires |
|---|------|---------|----------|
| 1 | 8.5 | No checkpoint with --resume (quick fail) | No checkpoint file |
| 2 | 8.1 | **Happy path** (full apply) | Valid config, test layer |
| 3 | 8.2 | Checkpoint resume (interactive) | From Test 8.1 |
| 4 | 8.3 | Checkpoint ignore (--no-resume) | From Test 8.1 |
| 5 | 8.4 | Checkpoint auto-resume (--resume) | From Test 8.1 |
| 6 | 8.6 | Null preservation | Captured records with nulls |
| 7 | 8.7 | Notes appending | Auth records with existing notes |
| 8 | 8.8 | Batch processing | Many records |
| 9 | 8.9 | Failed record handling | Records that will fail |
| 10 | 8.10 | Checkpoint preservation on failure | From Test 8.9 |
| 11 | 8.11 | Resume after partial failure | From Test 8.10 |
| 12 | 8.12 | Notes truncation | Long notes |
| 13 | 8.13 | **Full flow** | Valid config, collision scenario |
| 14 | 8.14 | Batch failure fallback | Records that batch-fail |

---

### Notes for Phase 8 testing

1. **Use a test layer** — Phase 8 writes live data to AGOL. Never test against production layers without explicit approval.

2. **Verify before and after** — Always record the current state of the authoritative layer before running apply tests.

3. **Restore after testing** — After Phase 8 tests, restore the layer from the backup created during the test:
   ```
   python conflate.py --layer "TestLayer" --restore
   ```

4. **Checkpoint files** — After testing, clean up checkpoint files:
   ```powershell
   Remove-Item backup\*checkpoint*.json -Force
   ```

5. **Config `apply` section** — Ensure `config.json` includes:
   ```json
   {
     "apply": {
       "batch_size": 50,
       "max_retries": 3
     }
   }
   ```

---

## Part H: Phase 9 — Attachment Migration (`conflate.py`)

**Status:** COMPLETE ✅

### Prerequisites for Phase 9 tests
- `config.local.json` must exist with valid credentials and layer URLs
- `config.json` must exist with valid JSON (matching thresholds and paths)
- Layers referenced in config must have features with valid geometries
- **Captured layer must have attachments** on some records (images, PDFs, etc.)
- **Authoritative layer must have attachments enabled** (`hasAttachments: true`)
- Python environment must have `geopandas`, `pyproj`, `requests` installed
- Phase 8 tests must pass (apply changes working)

---

### Test 9.1: Dry Run — Attachment Query (No Upload)

**Setup:** Valid config, captured layer has attachments on matched records

**Steps:**
1. Run: `python conflate.py --layer "AttachmentDryTest" --migrate-attachments` (dry run, no --apply)
2. Verify no attachments are downloaded or uploaded

**Expected output:**
```
Mode: DRY RUN — No changes will be written
Layer: AttachmentDryTest
Captured layer: <captured_name>
Authoritative layer: <auth_name>
...
Matching complete: <c> clean, <a> ambiguous, <n> new
Writing review file...
Review file created: backup/AttachmentDryTest_conflation_review.gpkg
Writing CSV report...
Report written: reports/AttachmentDryTest_<timestamp>.csv
=== Conflation Summary ===
Matched (clean):     <c>
Matched (ambiguous): <a>
New:                 <n>
Attachments pending: <count>
Total:               <total>
```

**Verification:**
```powershell
# Check review file has proposed_attachments table with "pending" status
sqlite3 "backup\AttachmentDryTest_conflation_review.gpkg" "SELECT status, COUNT(*) FROM proposed_attachments GROUP BY status"
# Should show: pending|<count>

# Check CSV report has attachment counts/names
Get-Content "reports\AttachmentDryTest_*.csv" | Select-String "attachment_count|attachment_names"
# Should show non-zero attachment_count for matched records
```

---

### Test 9.2: Apply — Attachment Migration (Happy Path)

**Setup:** Valid config, captured layer has attachments on matched records, auth layer has no existing attachments

**Steps:**
1. Run: `python conflate.py --layer "AttachmentApplyTest" --apply --migrate-attachments --no-resume`
2. Verify attachments appear on auth records

**Expected output:**
```
...
Phase 8: Applying changes to AGOL...
Updating batch 1/1 (N records)...
  Batch 1: N updated successfully
Appending batch 1/1 (M records)...
  Batch 1: M appended successfully
Checkpoint deleted — all changes applied successfully

Phase 9: Migrating attachments...
Migrated attachment 'photo1.jpg' (2048 bytes, image/jpeg) from OBJECTID 1 to GlobalID {aaa}
Migrated attachment 'doc1.pdf' (4096 bytes, application/pdf) from OBJECTID 1 to GlobalID {aaa}
Attachment migration complete: 2 migrated, 0 skipped, 0 failed
```

**Verification:**
```powershell
# Check attachments exist on auth record via ArcGIS API
# (requires arcgis API call or ArcGIS Pro)
# Verify attachment count on auth record matches captured record
```

---

### Test 9.3: Apply — Skip Existing Attachments

**Setup:** Valid config, captured layer has attachments, auth record already has some of the same attachments

**Steps:**
1. Run: `python conflate.py --layer "AttachmentSkipTest" --apply --migrate-attachments --no-resume`

**Expected output:**
```
Phase 9: Migrating attachments...
Attachment 'photo1.jpg' already exists on GlobalID {aaa}, skipping
Migrated attachment 'doc2.pdf' (1024 bytes, application/pdf) from OBJECTID 2 to GlobalID {bbb}
Attachment migration complete: 1 migrated, 1 skipped, 0 failed
```

**Verification:**
- Attachment with same name is skipped (not duplicated)
- New attachments are migrated
- Summary counts: migrated + skipped = total attachments on captured records

---

### Test 9.4: Apply — Independent Failure Handling

**Setup:** Valid config, captured layer has multiple attachments on a record, one attachment causes failure (e.g., corrupted or large)

**Steps:**
1. Run: `python conflate.py --layer "AttachmentFailTest" --apply --migrate-attachments --no-resume`

**Expected output:**
```
Phase 9: Migrating attachments...
Migrated attachment 'photo1.jpg' (2048 bytes, image/jpeg) from OBJECTID 1 to GlobalID {aaa}
ERROR: Failed to migrate attachment 'corrupt.dat' from OBJECTID 1 to GlobalID {aaa}: <error>
Migrated attachment 'photo2.jpg' (1024 bytes, image/jpeg) from OBJECTID 1 to GlobalID {aaa}
Attachment migration complete: 2 migrated, 0 skipped, 1 failed
```

**Verification:**
- Failed attachment does NOT abort migration of other attachments
- Error is logged with attachment name and source OBJECTID
- Summary counts are accurate: migrated + skipped + failed = total

---

### Test 9.5: Apply — No Attachments on Captured Record

**Setup:** Valid config, captured layer has records with no attachments

**Steps:**
1. Run: `python conflate.py --layer "NoAttTest" --apply --migrate-attachments --no-resume`

**Expected output:**
```
Phase 9: Migrating attachments...
Attachment migration complete: 0 migrated, 0 skipped, 0 failed
```

**Verification:**
- No errors or warnings
- Summary shows all zeros

---

### Test 9.6: Apply — Captured Record Has No Attachments Enabled

**Setup:** Valid config, captured layer does NOT have attachments enabled

**Steps:**
1. Run: `python conflate.py --layer "NoAttLayerTest" --apply --migrate-attachments --no-resume`

**Expected output:**
```
...
Phase 9: Migrating attachments...
Attachment migration complete: 0 migrated, 0 skipped, 0 failed
```

**Verification:**
- No errors when querying attachments on a layer without attachments enabled
- Graceful handling (warning logged, migration continues)

---

### Test 9.7: Apply — Review File Updated with Migration Status

**Setup:** Valid config, captured layer has attachments

**Steps:**
1. Run: `python conflate.py --layer "ReviewUpdateTest" --apply --migrate-attachments --no-resume`
2. Check the review GeoPackage after migration

**Verification:**
```powershell
# Check proposed_attachments table has updated statuses
sqlite3 "backup\ReviewUpdateTest_conflation_review.gpkg" "SELECT status, attachment_name FROM proposed_attachments"
# Should show: migrated|photo1.jpg, migrated|doc1.pdf, etc.
# NOT: pending|photo1.jpg
```

**Expected:**
- `proposed_attachments` table status updated from "pending" to "migrated" (or "skipped"/"failed")
- All migrated attachments have status = "migrated"

---

### Test 9.8: Apply — Captured Attachments NOT Deleted

**Setup:** Valid config, captured layer has attachments

**Steps:**
1. Record attachment count on captured record before migration
2. Run: `python conflate.py --layer "NoDeleteTest" --apply --migrate-attachments --no-resume`
3. Check attachment count on captured record after migration

**Expected:**
- Captured record still has all its original attachments
- Attachments are copied, not moved

**Verification:**
```powershell
# Verify captured layer attachments unchanged (via ArcGIS API)
# attachment_count on captured record should be same before and after
```

---

### Test 9.9: CSV Report — Attachment Data

**Setup:** Valid config, captured layer has attachments on matched records

**Steps:**
1. Run: `python conflate.py --layer "CsvReportTest" --migrate-attachments` (dry run)
2. Check the CSV report

**Verification:**
```powershell
# Check CSV has non-zero attachment_count for matched records
Import-Csv "reports\CsvReportTest_*.csv" | Where-Object { $_.match_type -in @("clean","ambiguous") } | Select-Object captured_objectid, attachment_count, attachment_names
# Should show: captured_objectid=1, attachment_count=2, attachment_names="photo1.jpg; doc1.pdf"
```

**Expected:**
- `attachment_count` column shows actual count (not 0)
- `attachment_names` column shows semicolon-separated list of attachment names

---

### Test 9.10: Full Flow Through Phase 9

**Setup:** Valid config, both layers with data, captured layer has attachments on some matched records

**Steps:**
1. Run: `python conflate.py --layer "FullFlowPhase9" --apply --migrate-attachments --no-resume`
2. Capture all output

**Expected output sequence:**
```
Mode: APPLY — Changes will be written to AGOL
...
Phase 8: Applying changes to AGOL...
Updating batch 1/1 (N records)...
  Batch 1: N updated successfully
Appending batch 1/1 (M records)...
  Batch 1: M appended successfully
Checkpoint deleted — all changes applied successfully

Phase 9: Migrating attachments...
Migrated attachment 'photo1.jpg' (2048 bytes, image/jpeg) from OBJECTID 1 to GlobalID {aaa}
Attachment 'doc1.pdf' already exists on GlobalID {bbb}, skipping
ERROR: Failed to migrate attachment 'bad.dat' from OBJECTID 2 to GlobalID {ccc}: <error>
Attachment migration complete: 15 migrated, 3 skipped, 1 failed
```

**Verification:**
- All Phase 4-8 lines present in correct order
- Phase 9 output present with migration details
- Summary counts are accurate
- Review file updated with migration statuses
- CSV report has attachment data
- Captured attachments preserved

---

### Phase 9 (Attachment Migration)

| # | Test | Purpose | Requires |
|---|------|---------|----------|
| 1 | 9.5 | No attachments on captured (quick pass) | Layer without attachments |
| 2 | 9.3 | Skip existing attachments | Auth with existing attachments |
| 3 | 9.1 | **Dry run — attachment query** | Captured with attachments |
| 4 | 9.2 | **Happy path — migrate attachments** | Captured with attachments |
| 5 | 9.4 | Independent failure handling | Multiple attachments, one fails |
| 6 | 9.6 | Layer without attachments enabled | Captured without attachments |
| 7 | 9.7 | Review file status update | Captured with attachments |
| 8 | 9.8 | Captured attachments NOT deleted | Captured with attachments |
| 9 | 9.9 | CSV report attachment data | Captured with attachments |
| 10 | 9.10 | **Full flow** | Valid config, collision + attachments |

---

### Notes for Phase 9 testing

1. **Use a test layer with attachments** — Phase 9 writes attachments to AGOL. Test against non-production layers.

2. **Verify attachment sizes** — Large attachments (>100MB) may cause timeouts or failures. Log warnings for very large files.

3. **Check MIME types** — Verify that attachment MIME types are preserved during migration.

4. **Dedup behavior** — If an attachment with the same name already exists on the auth record, it is skipped (not overwritten).

5. **Restore after testing** — After Phase 9 tests, restore the layer from backup and remove migrated attachments:
   ```
   python conflate.py --layer "TestLayer" --restore
   ```

6. **Attachment migration is independent** — Failed attachments do not abort the migration of other attachments or the overall conflation process.


