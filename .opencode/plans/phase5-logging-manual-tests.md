# Phase 5: Logging + Manual Tests Plan

## Status: ✅ COMPLETE

## Objective
Add console logging for per-point match results and create manual test documentation for Phase 5.

## Changes

### Change 1: Add `logging.basicConfig()` to `main()` in `conflate.py`

**File:** `data-conflation/conflate.py`  
**Location:** Inside `main()` function, after the docstring (line ~829)

**Before:**
```python
def main(argv=None):
    """Main entry point for the conflation tool."""
    # Parse CLI arguments
    args = parse_args(argv)
```

**After:**
```python
def main(argv=None):
    """Main entry point for the conflation tool."""
    # Configure logging for console output
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Parse CLI arguments
    args = parse_args(argv)
```

**Effect:** The `logger.info()` calls in `match_points()` will now produce console output:
```
INFO: Matched OBJECTID 5: clean (d1=3.2 ft, d2=inf ft)
INFO: Matched OBJECTID 8: ambiguous (d1=2.1 ft, d2=1.8 ft)
INFO: New OBJECTID 12: no match within 9 ft (nearest: 45.3 ft)
```

---

### Change 2: Add Part E to `manual_testing.md`

**File:** `data-conflation/manual_testing.md`  
**Location:** Insert after Part D (line 982), before the Cleanup section (line 984)

**Content to add:**

```markdown
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
```

---

## Execution Order

1. **Add `logging.basicConfig()`** to `conflate.py` (Change 1)
2. **Add Part E** to `manual_testing.md` (Change 2)
3. **Run tests** to verify logging change doesn't break anything

## Test Criteria

- `logging.basicConfig()` produces `INFO:` prefixed lines for per-point match logs
- Manual tests cover all classification rules and edge cases
- Full flow test shows complete output from Phase 4 through Phase 5
- Config tuning tests (5.10, 5.11) demonstrate that classifications change with config values
