# Data Conflation Tool — Design Review (Combined)

Merged from `conflation-plan-review.md` and `conflation-plan-review-local.md`. Overlapping points from both reviews have been combined into one entry; nothing from either source is dropped.

## Strengths

- **Dry-run-first workflow** is the right call for a destructive operation — the 4-table GeoPackage review file is well-designed.
- **Ambiguity detection** (2nd nearest neighbor within 20%) is smart and prevents silent wrong matches.
- **Backup before changes** with GUID preservation is the correct safety pattern.
- **UTM auto-detection** avoids hardcoding coordinates for any location.
- **Per-layer isolation** prevents cascade failures.
- Overall the plan is solid — clear phases, sensible safety net (backup/dry-run/restore), reasonable CRS handling for a single-town scope.

## Must-fix (silent data loss risk)

**1. Match collision — no captured→authoritative uniqueness constraint (many-to-one collision).**
The ambiguity check only looks one direction: "does this captured point have two nearby authoritative candidates?" It never checks the mirror case — two *different* captured points both landing within threshold of the *same* authoritative feature (e.g., both within 9 ft), each independently classified as a clean match. On `--apply`, both updates hit the same GlobalID; the second overwrites the first (last write wins), and one real feature effectively disappears instead of being flagged or appended as new. This is the sharpest edge in the design — worth a global assignment step (e.g., track claimed authoritative IDs, or run mutual-nearest-neighbor) rather than per-captured-point lookups in isolation, or at minimum detecting and flagging this "many-to-one" collision.

**2. The `notes` field is assumed, not verified — and could truncate data.**
The whole "mismatched fields → notes field" strategy is load-bearing for schema mismatches, but nothing in the plan checks that a `notes` field actually exists on the authoritative schema, or what its max length is. If it's missing, every mismatch write silently fails (or errors, depending on the arcgis SDK's behavior). If a captured record has many fields not present in the authoritative schema, the pipe-separated/concatenated notes string could exceed the field's max length (AGOL text fields often cap at 255) and silently truncate. Add a schema-validation step in setup or at the top of `conflate.py`, and validate/truncate with a warning at write time.

**3. Attachment migration is underspecified.**
The arcgis Python API's attachment APIs (`query_attachments`, `add_attachments`, etc.) are verbose and error-prone. The plan mentions downloading and re-uploading but doesn't address:
- Binary data handling in the API calls.
- What happens if an attachment exceeds AGOL's size limits.
- Whether the `arcgis.features` module even supports attachment operations in the version being used.

Consider implementing attachment migration as a separate optional step (`--migrate-attachments`) so the core conflation workflow works even if attachments cause issues.

## Decide-before-building (idempotency / operational fragility)

**4. Apply isn't idempotent.**
If `--apply` is run twice on the same layer (retry after a partial failure, accidental double-run):
- Does `notes` get rebuilt fresh from source each time, or appended onto existing content? If the latter, reruns duplicate the same text.
- Attachments: nothing dedupes already-migrated attachments, so a rerun would re-upload them.

**5. Restore mechanism is vague / single-slot backup isn't a true undo.**
`--restore` says "load backup from GeoPackage and replace authoritative layer" but doesn't specify how. `edit_features(updates=...)` requires matching on GlobalID — the plan should confirm the GeoPackage round-trip preserves OBJECTIDs and GlobalIDs correctly. Separately, `backup/{layer}_backup.gpkg` gets overwritten on every run: if apply is run twice, the backup taken before the second run already reflects the post-first-apply state, so restore can't get back to the true original. Restore also reloads authoritative geometry/attributes but doesn't reverse attachment migrations, so it's a partial rollback. Also verify, don't assume: reinserting original GlobalIDs on restore requires the hosted layer to accept client-supplied GlobalIDs (`use_global_ids`) — not all AGOL layers are configured to allow that. Worth confirming per-layer before relying on restore.

**6. Partial-apply within a layer.**
"Each layer processed independently" covers cross-layer isolation, but if apply dies midway through one layer's updates, that layer is left half-modified with backup/restore as the only recovery path — which per #5 may itself be unreliable.

**7. `proposed_attachments` table is disconnected from the workflow.**
It's listed as a review table but users can't selectively skip attachments in dry-run mode. Consider either making it informational-only or adding a filter mechanism.

**8. No handling for null geometries.**
The edge cases table doesn't mention null/empty geometries in captured data. These should be skipped with a warning.

## Worth confirming with stakeholders

**9. Field-name mapping assumption.** The plan matches attributes by exact field name between captured and authoritative schemas. If the 7 layers were captured with different field-naming conventions than the AGOL source, this silently sends everything to `notes` instead of updating real fields.

**10. One global threshold/ambiguity config for all 7 layers.** A single `threshold_ft: 9` and `ambiguity_pct: 20` apply uniformly. If the layers are heterogeneous (e.g., signs vs. hydrants vs. utility points), one threshold may not fit all — consider a per-layer override section in `config.json`.

**11. CRS: storing dual geometries adds complexity.** Reprojecting to UTM for matching then storing WGS 84 in the review file adds overhead. Since ArcGIS Pro handles on-the-fly reprojection, everything could be stored in WGS 84 with UTM used only transiently for distance calculations.

## Minor

**12. `rtree` dependency.** Given sub-1000 points per layer, `rtree` (which needs the `libspatialindex` native binary — a known pain point on Windows) is more than needed. `geopandas`'s built-in `.sindex` or `scipy.spatial.cKDTree` would do the same job without an extra native dependency.

---

## Verdict

Solid design overall. The biggest risk areas are:
- **Match collision (#1)** and **notes field assumptions (#2)** — these actually threaten data (silent loss/truncation) and should be resolved before writing matching code.
- **Attachment migration (#3)** — underspecified enough that it's worth isolating behind its own flag (`--migrate-attachments`) so a problem there doesn't block the core conflation workflow.

The remaining idempotency/restore items (#4–#8) are good to decide now but won't corrupt data if deferred. Stakeholder-confirmation items (#9–#11) and the minor dependency note (#12) can be resolved on a normal timeline.
