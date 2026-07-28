# data-conflation

A command-line tool that conflates a "captured" (field-collected) ArcGIS
Online feature layer into an "authoritative" (system-of-record) feature
layer: it spatially matches each captured point feature to the nearest
authoritative feature within a configurable distance, fills in null
attributes on the authoritative feature from the captured feature, copies
file attachments across, and appends captured features that don't match
anything as brand-new authoritative features. Every real (`--apply`) run can
later be undone with `--rollback`.

For a full function-by-function reference of every module (useful when
extending or debugging the tool), see [`docs/REFERENCE.md`](docs/REFERENCE.md).

## Requirements & installation

- Python 3.10+ (the codebase uses `X | None` union type hints).
- Install dependencies:

  ```
  pip install -r requirements.txt
  ```

  This installs `arcgis>=2.1.0` (ArcGIS API for Python — AGOL connectivity,
  querying, editing, attachments), `pyproj>=3.6.0` (geodesic distance
  calculations), and `pytest>=8.0.0` (test suite).

## Configuration

The tool reads two JSON config files from the current working directory:
`config.json` (which layers to conflate, tracked in git) and
`config.local.json` (AGOL credentials, **not** tracked in git).

### `config.json`

A top-level `"layers"` object maps a layer name — used as the CLI's
`--layer` value, the ledger filename stem, and the report/backup filename
prefix — to a layer-config object. Every layer entry supports these keys:

| Key | Required | Description |
|---|---|---|
| `authoritative_url` | Yes | REST URL of the authoritative (system-of-record) AGOL FeatureLayer, the one that gets written to. |
| `captured_url` | Yes | REST URL of the captured/collected AGOL FeatureLayer to conflate into the authoritative one. |
| `match_threshold_m` | Yes | Maximum geodesic distance, in meters, for a captured feature to be matched to an existing authoritative feature. Beyond this distance, the captured feature is appended as a brand-new authoritative feature instead. |
| `field_map` | Yes (may be `{}`) | Maps a captured field name → authoritative field name, for fields whose names differ between the two layers. Same-named fields on both layers are matched automatically without needing an entry here. |
| `copy_attachments` | Yes | Whether to copy file attachments from the captured feature to the authoritative feature after a successful write. If `true`, the authoritative layer must have attachments enabled (checked at startup). |
| `type_field_authoritative` / `type_field_captured` | No — must be given together, or not at all | Field names used for an extra type-equality check during matching, on top of spatial proximity (e.g. an asset-type field). Most layers don't need this and rely on distance alone. |

Example (values are placeholders — substitute your own org's service URLs):

```json
{
  "layers": {
    "hydrants": {
      "authoritative_url": "https://services.arcgis.com/PLACEHOLDER/arcgis/rest/services/authoritative_water_system/FeatureServer/8",
      "captured_url": "https://services.arcgis.com/PLACEHOLDER/arcgis/rest/services/collected_water_system/FeatureServer/8",
      "match_threshold_m": 10.67,
      "field_map": {},
      "copy_attachments": true
    },
    "water_network_structures": {
      "authoritative_url": "https://services.arcgis.com/PLACEHOLDER/arcgis/rest/services/authoritative_water_system/FeatureServer/10",
      "captured_url": "https://services.arcgis.com/PLACEHOLDER/arcgis/rest/services/collected_water_system/FeatureServer/10",
      "match_threshold_m": 10.67,
      "type_field_authoritative": "STRUCTTYPE",
      "type_field_captured": "STRUCTTYPE",
      "field_map": {},
      "copy_attachments": true
    }
  }
}
```

Both `authoritative_url` and `captured_url` layers must be point layers
(`esriGeometryPoint`) — the tool validates this at startup and refuses to run
against line/polygon layers.

### `config.local.json`

Not committed. Copy one of the two example templates and fill in your own
credentials:

- **`config.local.json.example`** — plain AGOL username/password ("builtin") auth:

  ```json
  {
    "portal_url": "https://www.arcgis.com",
    "username": "your_username_here",
    "password": "your_password_here"
  }
  ```

- **`config.local.oauth.json.example`** — OAuth auth, required for orgs whose
  logins are federated through an identity provider (e.g. ADFS/SAML), which
  reject a directly-POSTed password even when it's correct:

  ```json
  {
    "portal_url": "https://your-org.maps.arcgis.com/",
    "auth_type": "oauth",
    "client_id": "your_registered_app_client_id",
    "profile": "your_profile_name"
  }
  ```

  The first run under a given `profile` name opens a browser for an
  interactive login; every subsequent run (including unattended/automated
  ones) silently reuses the cached refresh token. If a cached profile's
  refresh token has expired, an automated run will fail with an error
  telling you to re-run manually once to re-authenticate.

## Usage

The tool has a single entry point, `conflate_main.py`, with three modes of
operation selected by flags:

```
python conflate_main.py --layer <name> [--apply] [--rollback BACKUP_FILE [--force]] [--backup-dir backups] [--report-dir reports]
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--layer` | Yes | — | Layer name/key from `config.json`'s `"layers"` object. |
| `--apply` | No | off | Without it: dry run only. With it: perform real writes to AGOL. |
| `--rollback BACKUP_FILE` | No | — | Path to a backup JSON file from a prior `--apply` run. If given, undoes that run instead of doing a normal conflation run. |
| `--force` | No | off | Only relevant with `--rollback`: bypasses the layer-identity safety check for a legacy backup/report that predates it. See [Safety notes](#safety-notes--troubleshooting). |
| `--backup-dir` | No | `backups` | Directory to write (apply) / read (rollback) backup JSON files. |
| `--report-dir` | No | `reports` | Directory to write report CSVs (and, for rollback, the rollback's own audit-log JSON). |

### Dry run (default)

```
python conflate_main.py --layer hydrants
```

Fetches both layers, computes matches, and writes a report CSV describing
what *would* happen — no writes to AGOL, no ledger changes. Use this to
preview a run before committing to it.

### Apply

```
python conflate_main.py --layer hydrants --apply
```

Does everything the dry run does, plus: backs up every authoritative feature
about to be updated, writes the updates/appends to AGOL, copies attachments
(if configured), records successfully-processed captured features in the
ledger, and writes a report CSV of actual outcomes.

### Rollback

```
python conflate_main.py --layer hydrants --rollback backups/hydrants_20260727_100000.json
```

Undoes a specific prior `--apply` run: restores updated authoritative
features to their pre-edit snapshot, deletes features that were appended,
removes attachments that were copied during that run, clears the
corresponding ledger entries (so those captured features are reconsidered on
a future run), and writes a JSON audit log of everything it did.

## Workflow lifecycle

```
dry run  ──(preview only, no state changes)

apply ──▶ backup (pre-edit snapshot)
      ──▶ write updates/appends to AGOL
      ──▶ copy attachments (if enabled)
      ──▶ ledger successes (skip on future runs)
      ──▶ write outcomes report

rollback (given a backup + its paired report) ──▶ restore/delete features
      ──▶ delete attachments added by that run
      ──▶ verify restored features against live AGOL
      ──▶ clear ledger entries for that run's captured features
      ──▶ write rollback audit log JSON
```

The **ledger** (one JSON file per layer, in `state/`) is the one piece of
state that spans separate invocations of the tool. It does two things:

1. **Skips already-processed captured features** — once a captured feature
   has been fully processed (write + attachment copy both succeeded), it's
   never reconsidered on a later run. A feature whose write or attachment
   copy only *partially* succeeded is deliberately left off the ledger, so
   it's retried automatically the next time the tool runs.
2. **Enforces one-to-one matching across runs** — once an authoritative
   feature has been matched/updated by some captured feature (this run or a
   prior one), it can never be claimed by a *different* captured feature
   later, preventing two captured features from silently merging into the
   same authoritative record.

Rolling back a run clears the corresponding ledger entries, so those
captured features become eligible for reprocessing again.

## Directory layout

| Path | Contents |
|---|---|
| `state/<layer>.json` | The ledger for that layer — which captured features (by GlobalID) have been fully processed, and what they were matched/appended to. |
| `backups/<layer>_<timestamp>.json` | Pre-edit snapshot of every authoritative feature an apply run updated. Required input to `--rollback`. |
| `reports/<layer>_<timestamp>.csv` | Outcome report for a run (dry-run or apply). Shares the same `<timestamp>` as its paired backup file. Required input to `--rollback`, alongside the backup. |
| `reports/<layer>_<apply-timestamp>_rollback_<rollback-timestamp>.json` | Audit log written by a `--rollback` run: what it restored/deleted, attachment cleanup results, before/after feature counts, and post-restore verification results. Nothing in the tool reads this back — it's a durable record only. |

Filenames pair up by their shared `<layer>_<timestamp>` stem: a backup and
its report from the same apply run always have matching stems (just
different extensions), which is how `--rollback backups/hydrants_<ts>.json`
locates the report it needs without a separate flag.

## Safety notes & troubleshooting

- **Layer-mismatch protection.** `--rollback` refuses to run if the
  backup/report file's recorded layer doesn't match the `--layer` value you
  passed, raising an error before touching AGOL at all — this guards against
  accidentally rolling back the wrong live layer due to a `--layer` typo or a
  stale/misfiled backup path. Backups/reports from before this check existed
  have no recorded layer identity at all and are refused by default too;
  `--force` bypasses this only after you've manually confirmed the
  backup/report really were produced for the `--layer` you're passing.
- **Attachment copy failures self-heal.** If a feature's write succeeds but
  its attachment copy only partially succeeds (or fails to even list source
  attachments), that feature is *not* ledgered — it's retried automatically
  next run, and attachments already successfully copied won't be
  re-uploaded.
- **Point geometry only.** Both the authoritative and captured layers must
  be point layers. Non-point or missing-geometry features in the captured
  layer are skipped (reported as `skipped_no_geometry`) rather than crashing
  the run.
- **Running the test suite:**

  ```
  pytest
  ```
