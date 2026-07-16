# Manual Test Suite — Phase 1 & 2 Combined

## Prerequisites

Before running any tests, ensure:

1. **Python environment** — Python 3.9+ installed with dependencies from `requirements.txt`
2. **AGOL credentials** — Valid username/password for `arcgis.com`
3. **Two Feature Layer URLs** — Full `FeatureServer/0` endpoints (can use the same layer tested twice, or two different ones)
4. **Clean workspace** — No existing `config.local.json` for Phase 1 tests; no leftover backups for Phase 2 tests

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

### Test 1.7: Wrong Layer Type
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py`
2. Enter valid credentials
3. Enter a URL that points to a **non-Feature Layer** (e.g., Map Service, Table, or Web Map)
4. When prompted `"Retry? [y/N]:"` → type `y`
5. Enter a valid Feature Layer URL (captured)
6. Enter another valid Feature Layer URL (auth)

**Expected:**
- First URL rejected with: `"Found type: '<type>'. Expected 'Feature Layer'."`
- Retry prompts appear
- Setup completes successfully after entering valid Feature Layer URLs

---

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

### Test 2.17: Layer Not Accessible
**Setup:** Valid config, but layer URL is invalid/unreachable

**Steps:**
1. Modify `config.local.json` to point to a non-existent layer
2. Run: `python conflate.py --layer "TestLayer"`

**Expected:**
- Output: `"Layer not found or not accessible: <url>"`
- Error details printed
- Script exits with code 1

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
**Setup:** Valid config

**Steps:**
1. Run: `python conflate.py --layer "TimestampTest" --apply`
2. Wait 2 seconds
3. Run: `python conflate.py --layer "TimestampTest" --apply`

**Expected:**
- Each run generates a unique timestamp
- No file overwrites between runs
- Backup files have distinct timestamps

**Verification:**
```powershell
Get-ChildItem backup\TimestampTest_backup_*
# Should show two files with different timestamps
```

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
| 8 | 1.7 | Wrong layer type | Valid credentials |
| 9 | 1.9 | Config file permissions | From Test 1.1 |
| 10 | 1.10 | Content preservation on failure | From Test 1.1 |

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
| 17 | 2.17 | Layer not accessible | Modify config.local.json |
| 18 | 2.18 | Auto-open flag | From Test 2.3 |
| 19 | 2.19 | Special characters in layer name | From Test 2.3 |
| 20 | 2.20 | Timestamp uniqueness | From Test 2.3 |

---

## Cleanup

After all tests pass:

```powershell
# Remove test config (contains real credentials)
Remove-Item config.local.json -Force

# Remove test backups and reports
Remove-Item backup\* -Recurse -Force
Remove-Item reports\* -Force

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
| 2.17 | Invalid layer URL | `--layer "X"` |
