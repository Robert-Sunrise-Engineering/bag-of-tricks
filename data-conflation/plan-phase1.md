# Phase 1: Project Scaffolding & Configuration

## Objective
Set up the project structure, dependency definitions, shared configuration, and interactive setup script for creating local configuration with AGOL credentials.

## Deliverables

### 1. Project Structure
Create the following directory and file layout:

```
data-conflation/
├── conflate.py              # Main script (placeholder)
├── setup-config.py          # Interactive config creator
├── config.json              # Shared config (committed)
├── config.local.json        # Credentials (git-ignored)
├── requirements.txt         # Python dependencies
├── .gitignore               # Git ignore rules
├── backup/                  # Backups and review files
└── reports/                 # CSV reports
```

### 2. `requirements.txt`
Pin these dependencies:
```
arcgis>=2.1.0
geopandas>=0.14.0
pyproj>=3.6.0
pandas>=2.0.0
```

### 3. `.gitignore`
Ignore:
- `config.local.json` (contains credentials)
- `backup/` directory
- `reports/` directory
- Python cache: `__pycache__/`, `*.pyc`, `.pytest_cache/`
- IDE files: `.vscode/`, `.idea/`

### 4. `config.json` (committed, no secrets)
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

### 5. `setup-config.py` — Interactive Config Creator

This script runs once to create `config.local.json`. It must:

#### Step 1: Prompt for AGOL Credentials
- Prompt for AGOL username (plain input)
- Prompt for AGOL password (masked/hidden input using `getpass`)

#### Step 2: Prompt for Layer URLs
- Prompt for captured layer URL (full URL to FeatureServer/0 endpoint)
- Prompt for authoritative layer URL (full URL to FeatureServer/0 endpoint)

#### Step 3: Validate URLs
- Attempt to authenticate to AGOL using the provided credentials via `arcgis.gis.GIS`
- For each URL, fetch the layer item info to verify it is accessible and is a Feature Layer
- On validation failure, display the error and allow the user to retry that specific URL
- If both URLs are invalid, abort and exit with code 1

#### Step 4: Display Layer Information
- After successful validation, print:
  - Layer name for captured layer
  - Feature count for captured layer
  - Layer name for authoritative layer
  - Feature count for authoritative layer
  - List of field names for both layers (for user review)

#### Step 5: Write `config.local.json`
Write the following structure to `config.local.json` in the project root:
```json
{
  "agol": {
    "username": "<username>",
    "password": "<password>"
  },
  "captured_layer_url": "<full captured layer URL>",
  "auth_layer_url": "<full authoritative layer URL>"
}
```

#### Error Handling
- If `config.local.json` already exists, prompt: "config.local.json already exists. Overwrite? [y/N]"
- If AGOL authentication fails, display "Could not authenticate to AGOL. Please check your credentials." and exit with code 1
- If a URL does not resolve to a valid Feature Layer, display "URL does not point to a valid Feature Layer: <url>" and allow retry
- Use `getpass` for password input to avoid echoing on console

## Test Criteria
- `setup-config.py` runs interactively and writes a valid `config.local.json`
- Invalid credentials are rejected with a clear error message
- Invalid URLs are detected and rejected
- Overwrite protection works (defaults to no)
- `config.json` is valid JSON with correct structure
- `.gitignore` correctly excludes `config.local.json` and `backup/`, `reports/`
