# latfromservice

Generate water service laterals from service points.

For each service point, the tool finds the nearest main within a search
distance, appends a two-point lateral from the point to the main, and inserts a
vertex into the main at the connection so a trace network creates a junction.
Points that are already connected, sitting on a main, too far from any main, or
sharing a coordinate with another point are skipped and listed in a timestamped
report.

This is the current two-module rewrite (`lateral_core.py` + `lat_from_service.py`)
plus an ArcGIS Pro toolbox. It supersedes the earlier single-file
`lat_from_service.py` in this folder.

## Requirements

- ArcGIS Pro 3.x — tested with ArcGIS Pro 3.7.1 Standard (ArcEditor).
  `Near`, `Project`, and `arcpy.da.Editor` all work at that license level.
- The ArcGIS Pro Python environment. On Windows:

  ```powershell
  $py = "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
  ```

  `propy` is not always on `PATH`, so use the explicit path above.
- **Do not pass `-I`.** It removes the script folder from `sys.path`, so
  `import lateral_core` fails. `-E`/`-s` are also unnecessary.
- No `pip` dependencies. `arcpy` is provided by ArcGIS Pro.
- The laterals target must use a **projected** coordinate system.

## Files

| File | Purpose |
|---|---|
| `lateral_core.py` | Engine: analysis, main-vertex insertion, lateral construction, transactions, reporting, self-test |
| `lat_from_service.py` | Command-line entry point and ArcGIS Pro script-tool wrapper |
| `LateralTools.pyt` | ArcGIS Pro Python toolbox with one tool, **Generate Service Laterals** |
| `README.md` | This file |

Keep all three Python files in the same folder. `lat_from_service.py` and
`LateralTools.pyt` both import `lateral_core.py` from beside themselves.

## Quick start (command line)

```powershell
$py = "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"

# Analyse and report only; no writes
& $py .\lat_from_service.py `
  "C:\data\project.gdb\ServicePoints" `
  "C:\data\project.gdb\Mains" `
  "C:\data\project.gdb\Laterals" `
  "100 Feet" --preview --report-folder "C:\data\reports"

# Apply: append laterals and insert main vertices
& $py .\lat_from_service.py `
  "C:\data\project.gdb\ServicePoints" `
  "C:\data\project.gdb\Mains" `
  "C:\data\project.gdb\Laterals" `
  "100 Feet" --report-folder "C:\data\reports"
```

Always run `--preview` first and compare the reported counts with the previous
run or with your expectations.

## Options

```
lat_from_service.py points mains laterals [max_search_dist]
                    [-t/--tolerance TOL]
                    [--no-skip-connected]
                    [--no-vertices]
                    [--preview]
                    [--report-folder DIR]
                    [--no-transaction]
                    [--selftest]
```

| Argument | Default | Meaning |
|---|---|---|
| `points` | required | Service point feature class or layer |
| `mains` | required | Main line feature class or layer; vertices may be inserted |
| `laterals` | required | **Existing** line feature class to append into |
| `max_search_dist` | `100 Feet` | Maximum distance from a point to its nearest main. A linear unit (`"100 Feet"`, `"30 Meters"`) or a bare number in the laterals' units |
| `-t`, `--tolerance` | `1 Foot` | Connection tolerance: already-connected distance to an existing lateral, on-main distance, and main-vertex snap tolerance |
| `--no-skip-connected` | off | Do **not** skip points that already have a lateral. This duplicates laterals on reruns — leave off |
| `--no-vertices` | off | Do not insert vertices into mains. Lateral ends will meet the main at an arbitrary point, not a vertex |
| `--preview` | off | Run the full analysis and write the report, but make no geodatabase writes |
| `--report-folder` | Pro scratch folder | Folder for the timestamped report |
| `--no-transaction` | off | Do not wrap the writes in an `arcpy.da.Editor` transaction. Do not use on real data |
| `--selftest` | — | Run synthetic checks in the `memory` workspace and exit |

If the CLI is called with **no arguments**, it reads nine ArcGIS Pro script-tool
parameters via `arcpy.GetParameterAsText(0..8)`. The toolbox is the recommended
way to use it interactively.

## ArcGIS Pro toolbox

Add `LateralTools.pyt` to the project (Catalog → Toolboxes → Add Toolbox), then
run **Service Laterals → Generate Service Laterals**.

| # | Parameter | Type | Default |
|---|---|---|---|
| 1 | Service Points | Point feature layer | — |
| 2 | Mains | Polyline feature layer | — |
| 3 | Target Laterals (appended to) | Polyline feature layer | — |
| 4 | Maximum Search Distance | Linear unit | `100 Feet` |
| 5 | Connection Tolerance | Linear unit | `1 Foot` |
| 6 | Skip Points Already Connected | Boolean | `True` |
| 7 | Insert Vertices in Mains | Boolean | `True` |
| 8 | Preview Only (No Writes) | Boolean | `False` |
| 9 | Report Folder | Folder | Pro scratch folder |

The toolbox runs with `transactional=False` because ArcGIS Pro owns the edit
session. The command-line path defaults to `transactional=True`. For unattended
or scripted production runs, use the CLI so the transaction is explicit.

## How it works

1. **Validate** the three inputs: points must be `Point`, mains and laterals
   must be `Polyline`, the laterals target must be projected, and mains and
   laterals must be separate feature classes. Before any write, the tool checks
   schema locks on the geodatabase workspaces and fails early if ArcGIS Pro or
   another process holds them.
2. **Project** the points and mains into the laterals' coordinate system on
   temporary copies in the `memory` workspace.
3. **Analyse** each point with `arcpy.analysis.Near` (no search radius, so every
   point gets a true nearest-main distance for the report and percentiles).
4. **Classify** each point (see below).
5. **Resolve connection vertices**. For each connection, snap to an existing
   main vertex, snap to a vertex already planned in this run, or insert a new
   vertex at the closest segment. Multipart mains, Z/M values, and curves are
   handled; part structure is preserved and curves are flattened.
6. **Insert** a two-point lateral for every `build` point.
7. **Report** counts, distances, and skipped OIDs.

### Point classification

| Category | Meaning | Write |
|---|---|---|
| `build` | A lateral is created | Lateral + main vertex |
| `already_connected` | The point is within the connection tolerance of an existing lateral | None (makes reruns idempotent) |
| `on_main` | The point is within the connection tolerance of a main | Main vertex only; no zero-length lateral |
| `too_far` | No main, or the nearest main is beyond the maximum search distance | None |
| `duplicate_point` | Another service point has the same rounded coordinate; only the first gets a lateral | None for the later point |
| `bad_geom` | Null point geometry | None |

## Outputs

- New two-point laterals appended to the existing laterals feature class.
- Vertices inserted into the mains at the connection points (unless
  `--no-vertices`). The mains' feature count does not change.
- A timestamped text report:
  `lateral_report_YYYYmmdd_HHMMSS.txt` in `--report-folder`, or in the Pro
  scratch folder / OS temp folder when omitted. It contains:
  - input paths, units, and parameters;
  - the classification counts;
  - inserted / would-insert laterals and vertices;
  - vertices snapped to existing vertices;
  - unresolved connections and degenerate laterals;
  - p50/p80/p95/p97/p99 distance percentiles;
  - OID lists for `too_far`, `on_main`, `already_connected`, and duplicate points.
- Geoprocessing messages mirroring the counts and warnings.

**Only `SHAPE` is written to the laterals.** No attribute mapping is performed:
`FACILITYID`, `LINETYPE`, `MATERIAL`, and similar fields stay null unless the
geodatabase itself applies field defaults. If your model needs those values,
populate them in a separate step.

## Safety and operational notes

- **Back up the target geodatabase before writing.** The tool modifies mains in
  place by inserting vertices, and appends to laterals. A backup is the only
  clean rollback.
- **Close ArcGIS Pro or any open edit session** on the mains/laterals. On a
  file geodatabase the tool checks schema locks before writing and fails early
  if it cannot acquire them.
- **Keep `Skip Already Connected` on.** A second run should insert 0 laterals
  and 0 vertices. If a preview of a rerun is not 0/0, stop.
- **Do not use `--no-transaction` on real data.** On the CLI, both the main
  update and the lateral insert run inside one `arcpy.da.Editor` operation on
  the geodatabase; a failure aborts both. If the Editor cannot start on a
  `.gdb`, the current version raises an error instead of silently falling back
  to a non-transactional write.
- If the mains and laterals are in **different workspaces**, the transaction
  covers only the laterals workspace. The tool warns about this.
- For a target inside a **feature dataset** (for example a trace-network
  feature dataset), the engine resolves the containing `.gdb` for locking and
  the edit session. This is what allows the tool to write to
  `Default.gdb\TraceNetwork\Main_Copy` and `Laterals_Copy` transactionally.
- After editing data in a **trace network or geometric network**, validate or
  rebuild the network topology so the new connection vertices become junctions.
  The tool does not run network validation for you.
- Use `--preview` first; it performs the full analysis and writes only the
  report.

## Examples

Preview with a longer search distance and a tighter tolerance:

```powershell
& $py .\lat_from_service.py `
  "C:\data\project.gdb\WaterServiceConnections" `
  "C:\data\project.gdb\WaterMains" `
  "C:\data\project.gdb\WaterLaterals" `
  "150 Feet" -t "0.5 Feet" --preview --report-folder "C:\data\reports\preview"
```

Insert no main vertices (useful when the mains must not change):

```powershell
& $py .\lat_from_service.py `
  "C:\data\project.gdb\WaterServiceConnections" `
  "C:\data\project.gdb\WaterMains" `
  "C:\data\project.gdb\WaterLaterals" `
  "100 Feet" --no-vertices --report-folder "C:\data\reports"
```

Self-test (synthetic data, no inputs, nothing written outside the `memory`
workspace):

```powershell
& $py .\lat_from_service.py --selftest
```

Expected output ends with:

```text
SELFTEST PASSED
```

## Reference run

The tool was validated on a 2026 water-model dataset of 5,922 service points,
3,478 mains, and 846 existing laterals:

- 4,897 laterals inserted
- 4,533 main vertices inserted, 400 snapped to existing vertices
- 0 unresolved connections, 0 degenerate laterals
- classification: 4,897 build / 135 too far / 853 already connected /
  36 on main / 1 duplicate point / 0 bad geometry
- p50 distance 27.78 ft
- a rerun (preview and apply) inserted 0 / 0
- preview took about 10 s and apply about 15 s on that dataset

Treat these as a scale reference, not expected results for your data.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Cannot acquire a schema lock ...` | ArcGIS Pro or another process has the gdb open in an edit session. Close it and rerun. |
| `Could not open a transactional edit session ... refusing to fall back` | The edit session could not start. Close Pro/locks and check permissions. Do not use `--no-transaction` as a workaround. |
| `ImportError: No module named lateral_core` | Keep `lateral_core.py` beside `lat_from_service.py`, run from that folder, and do not pass `-I` to Python. |
| `arcpy` not found | Use the ArcGIS Pro Python executable, not a system Python. |
| Stale code after an edit | Delete `__pycache__` or ensure the tool folder is first on `sys.path`. |
| New laterals have null attributes | Expected: only `SHAPE` is written. Populate attributes separately if needed. |
| Trace network has no junctions at the connections | Validate/rebuild the network topology after the run. Also confirm `Insert Vertices in Mains` was on. |
| A rerun inserts duplicates | `Skip Points Already Connected` was turned off (`--no-skip-connected`). Restore from backup and rerun with the default. |

## Exit codes (CLI)

| Code | Meaning |
|---|---|
| `0` | Run completed successfully (preview or apply) |
| `1` | `arcpy.ExecuteError` or an unexpected exception; see the message/report |

## Notes on this version

- The engine, CLI, and toolbox are the versions used for the 2026 AVWSID master
  plan update run.
- `_workspace_of()` resolves a feature class inside a feature dataset to its
  containing `.gdb`, fixing schema-lock checks and the Editor multiuser flag
  for trace-network target feature classes.
- A file-geodatabase Editor failure is fail-closed: the tool raises instead of
  writing without a transaction.
- The AVWSID run also used a separate project-specific verification harness
  (counts, endpoints, GlobalIDs, network state). It is not included here because
  its feature-class names and expected counts are project-specific; the
  `--selftest` and the report are the reusable checks in this folder.
