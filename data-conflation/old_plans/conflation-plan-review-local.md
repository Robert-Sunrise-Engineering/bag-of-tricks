# Data Conflation Design Review

## Strengths
- **Dry-run-first workflow** is the right call for a destructive operation — the 4-table GeoPackage review file is well-designed
- **Ambiguity detection** (2nd nearest neighbor within 20%) is smart and prevents silent wrong matches
- **Backup before changes** with GUID preservation is the correct safety pattern
- **UTM auto-detection** avoids hardcoding coordinates for any location
- **Per-layer isolation** prevents cascade failures

## Concerns

**1. Attachment migration is underspecified**
The arcgis Python API's attachment APIs (`query_attachments`, `add_attachments`, etc.) are verbose and error-prone. The plan mentions downloading and re-uploading but doesn't address:
- Binary data handling in the API calls
- What happens if an attachment exceeds AGOL's size limits
- Whether the `arcgis.features` module even supports attachment operations in the version being used

**2. Restore mechanism is vague**
`--restore` says "load backup from GeoPackage and replace authoritative layer" but doesn't specify how. `edit_features(updates=...)` requires matching on GlobalID — the plan should confirm the GeoPackage round-trip preserves OBJECTIDs and GlobalIDs correctly.

**3. One captured point → one auth record: no reverse check**
If two captured points both match to the same authoritative record (both within 9 ft), the plan updates the auth record twice (last write wins). Consider detecting and flagging this "many-to-one" collision.

**4. Notes field could truncate data**
If a captured record has many fields not present in the authoritative schema, the pipe-separated notes string could exceed the `notes` field's max length. Should validate or truncate with a warning.

**5. `proposed_attachments` table is disconnected from the workflow**
It's listed as a review table but users can't selectively skip attachments in dry-run mode. Consider either making it informational-only or adding a filter mechanism.

**6. CRS: storing dual geometries adds complexity**
Reprojecting to UTM for matching then storing WGS 84 in the review file adds overhead. Since ArcGIS Pro handles on-the-fly reprojection, everything could be stored in WGS 84 with UTM used only transiently for distance calculations.

**7. No handling for null geometries**
The edge cases table doesn't mention null/empty geometries in captured data. These should be skipped with a warning.

## Verdict
Solid design overall. The biggest risk area is attachment migration — consider implementing that as a separate optional step (`--migrate-attachments`) so the core conflation workflow works even if attachments cause issues.
