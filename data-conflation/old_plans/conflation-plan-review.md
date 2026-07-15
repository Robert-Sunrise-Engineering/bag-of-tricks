# Data Conflation Tool — Design Review

Review of `conflation-plan.md` and `conversation-summary.md`. Overall the plan is solid — clear phases, sensible safety net (backup/dry-run/restore), reasonable CRS handling for a single-town scope. The items below are ranked by severity: fix before writing matching code, decide before building, or confirm with stakeholders.

## Must-fix (silent data loss risk)

**1. Match collision — no captured→authoritative uniqueness constraint.**
The ambiguity check only looks one direction: "does this captured point have two nearby authoritative candidates?" It never checks the mirror case — two *different* captured points both landing within threshold of the *same* authoritative feature, each independently classified as a clean match. On `--apply`, both updates hit the same GlobalID; the second overwrites the first, and one real feature effectively disappears instead of being flagged or appended as new. This is the sharpest edge in the design — worth a global assignment step (e.g., track claimed authoritative IDs, or run mutual-nearest-neighbor) rather than per-captured-point lookups in isolation.

**2. The `notes` field is assumed, not verified.**
The whole "mismatched fields → notes field" strategy is load-bearing for schema mismatches, but nothing in the plan checks that a `notes` field actually exists on the authoritative schema, or what its max length is. If it's missing, every mismatch write silently fails (or errors, depending on the arcgis SDK's behavior). If it exists but is short (AGOL text fields often cap at 255), concatenating several mismatched fields can silently truncate. Add a schema-validation step in setup or at the top of `conflate.py`.

## Decide-before-building (idempotency / operational fragility)

**3. Apply isn't idempotent.**
If `--apply` is run twice on the same layer (retry after a partial failure, accidental double-run):
- Does `notes` get rebuilt fresh from source each time, or appended onto existing content? If the latter, reruns duplicate the same text.
- Attachments: nothing dedupes already-migrated attachments, so a rerun would re-upload them.

**4. Single-slot backup + restore isn't a true undo.**
`backup/{layer}_backup.gpkg` gets overwritten on every run. If apply is run twice, the backup taken before the second run already reflects the post-first-apply state — restore can't get back to the true original. Also, restore reloads authoritative geometry/attributes but doesn't reverse attachment migrations, so it's a partial rollback. Separately (verify, don't assume): reinserting original GlobalIDs on restore requires the hosted layer to accept client-supplied GlobalIDs (`use_global_ids`) — not all AGOL layers are configured to allow that. Worth confirming per-layer before relying on restore.

**5. Partial-apply within a layer.**
"Each layer processed independently" covers cross-layer isolation, but if apply dies midway through one layer's updates, that layer is left half-modified with backup/restore as the only recovery path — which per #4 may itself be unreliable.

## Worth confirming with stakeholders

**6. Field-name mapping assumption.** The plan matches attributes by exact field name between captured and authoritative schemas. If the 7 layers were captured with different field-naming conventions than the AGOL source, this silently sends everything to `notes` instead of updating real fields.

**7. One global threshold/ambiguity config for all 7 layers.** A single `threshold_ft: 9` and `ambiguity_pct: 20` apply uniformly. If the layers are heterogeneous (e.g., signs vs. hydrants vs. utility points), one threshold may not fit all — consider a per-layer override section in `config.json`.

## Minor

**8. `rtree` dependency.** Given sub-1000 points per layer, `rtree` (which needs the `libspatialindex` native binary — a known pain point on Windows) is more than needed. `geopandas`'s built-in `.sindex` or `scipy.spatial.cKDTree` would do the same job without an extra native dependency.

---

**Priority to resolve before writing matching code:** #1 (silent overwrite/loss on match collision) and #2 (notes field assumption) — these actually threaten data. The rest are good to decide now but won't corrupt data if deferred.
