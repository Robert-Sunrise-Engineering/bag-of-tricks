# Manual Smoke Test Plan for setup_config.py

## Prerequisites

Before running, you will need:
1. **AGOL credentials** — a valid username/password for `arcgis.com`
2. **Two Feature Layer URLs** — full FeatureServer/0 endpoints (can be the same layer tested twice, or two different ones)
3. **A clean workspace** — no existing `config.local.json`

---

## Test Scenarios

### Scenario 1: Happy Path (Recommended)
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py`
2. Enter AGOL username
3. Enter AGOL password (hidden)
4. Enter captured layer URL
5. Enter authoritative layer URL
6. Wait for validation messages

**Expected:**
- "Authenticated as: <username>"
- "Validating Captured Layer..." -> shows layer name, type
- "Validating Authoritative Layer..." -> shows layer name, type
- "Layer Information" section with name, count, fields for both layers
- "Configuration written to config.local.json"
- "Setup complete!"

**Verification:**
```powershell
# Check the file exists and has correct structure
Get-Content config.local.json | ConvertFrom-Json | Format-List
```

**Expected JSON:**
```
agol      : @{username=<your_user>; password=<your_pass>}
captured_layer_url : <the captured URL you entered>
auth_layer_url     : <the auth URL you entered>
```

---

### Scenario 2: Overwrite Protection
**Setup:** `config.local.json` already exists (from Scenario 1)

**Steps:**
1. Run: `python setup_config.py`
2. When prompted "config.local.json already exists. Overwrite? [y/N]:" -> press Enter (no input)

**Expected:**
- "Aborted. No changes made."
- Script exits without modifying the file
- Original `config.local.json` content is preserved

---

### Scenario 3: Overwrite Accept
**Setup:** `config.local.json` exists

**Steps:**
1. Run: `python setup_config.py`
2. When prompted -> type `y` and Enter
3. Enter credentials and URLs again

**Expected:**
- New `config.local.json` is written with new values
- Script completes normally

---

### Scenario 4: Invalid Credentials
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py`
2. Enter a **wrong** username/password

**Expected:**
- "Could not authenticate to AGOL. Please check your credentials."
- Error details printed
- Script exits with code 1

---

### Scenario 5: Invalid URL (Non-existent Layer)
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py`
2. Enter valid credentials
3. Enter a **fake** URL (e.g., `https://services.arcgis.com/fake/FeatureServer/0`)
4. When prompted "Retry? [y/N]:" -> type `y`
5. Enter a **second fake** URL
6. When prompted "Retry? [y/N]:" -> type `y`

**Expected:**
- "URL does not point to a valid Feature Layer: <url>" for each
- "Both URLs are invalid. Aborting setup."
- Script exits with code 1

---

### Scenario 6: One Valid, One Invalid URL
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py`
2. Enter valid credentials
3. Enter a **valid** layer URL
4. Enter a **fake** URL
5. When prompted "Retry? [y/N]:" -> type `y`
6. Enter another **fake** URL
7. When prompted "Retry? [y/N]:" -> type `y`

**Expected:**
- First layer validates successfully
- Second layer fails validation with retry prompts
- "One or both URLs are invalid. Aborting setup."
- Script exits with code 1

---

### Scenario 7: Wrong Layer Type
**Setup:** No existing `config.local.json`

**Steps:**
1. Run: `python setup_config.py`
2. Enter valid credentials
3. Enter a URL that points to a **non-Feature Layer** (e.g., a Map Service or Web Map)
4. When prompted "Retry? [y/N]:" -> type `y`
5. Enter a **valid Feature Layer** URL
6. Enter another **valid Feature Layer** URL

**Expected:**
- First URL rejected with: "Found type: '<type>'. Expected 'Feature Layer'."
- Retry succeeds with valid URLs
- Setup completes normally

---

## Execution Order (Recommended)

Run in this sequence to minimize credential entry:

1. **Scenario 4** -> Invalid credentials (quick fail, no layers needed)
2. **Scenario 1** -> Happy path (full flow, creates `config.local.json`)
3. **Scenario 2** -> Overwrite protection (tests guard with existing file)
4. **Scenario 3** -> Overwrite accept (tests affirmative path)
5. **Scenario 5** -> Both invalid URLs (tests abort logic)
6. **Scenario 6** -> One valid, one invalid (tests partial failure)
7. **Scenario 7** -> Wrong layer type (tests type validation)

---

## Cleanup

After all scenarios pass:
```powershell
# Remove the test config (contains real credentials)
Remove-Item config.local.json -Force
```

---

## Quick Reference: Input Cheat Sheet

| Scenario | Username | Password | URL 1 | URL 2 | Extra inputs |
|---|---|---|---|---|---|
| 1 (Happy) | valid | valid | valid | valid | -- |
| 2 (Overwrite) | N/A | N/A | N/A | N/A | Enter (at overwrite prompt) |
| 3 (Overwrite+) | valid | valid | valid | valid | `y` (at overwrite prompt) |
| 4 (Bad creds) | wrong | wrong | -- | -- | -- |
| 5 (Both invalid) | valid | valid | fake | fake | `y`, `y` (at retries) |
| 6 (One invalid) | valid | valid | valid | fake | `y` (auth retry) |
| 7 (Wrong type) | valid | valid | wrong-type | valid | `y` (at retry) |
