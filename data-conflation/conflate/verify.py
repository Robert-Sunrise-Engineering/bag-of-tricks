"""Post-rollback verification against the live authoritative service.

rollback.py's restore_results only record whether AGOL *accepted* each
restore write (edit_features' reported success/failure) -- not whether the
live feature's state actually matches the backup snapshot it was restored
from. This module closes that gap: given a set of backup entries and the
live layer, it re-queries AGOL and diffs each restored feature's actual
attributes/geometry against what the backup expected.
"""

from conflate.fields import EXCLUDED_FIELDS
from conflate.geometry import geodesic_distance

# Keeps the "OBJECTID IN (...)" where clause to a sane size per request
# rather than assuming an unbounded list of OIDs is safe over POST.
_QUERY_BATCH_SIZE = 200

# A restore round-trips through fetch_all_features's out_sr=4326 request and
# back through the layer's native spatial reference, so bit-exact equality
# isn't expected -- some reprojection rounding is normal. This default is a
# starting point (validated against one real layer during implementation,
# not just these unit tests); pass geometry_tolerance_m explicitly to tune
# it for a specific layer's native SR precision.
DEFAULT_GEOMETRY_TOLERANCE_M = 0.5


def _as_dict(feature):
    if hasattr(feature, "as_dict") and callable(feature.as_dict):
        return feature.as_dict()
    return {"attributes": feature.attributes, "geometry": feature.geometry}


def _fetch_live_by_oid(layer, oids: list[int]) -> dict:
    """Query ``layer`` for the given OBJECTIDs, batched, returning
    ``{oid: {"attributes": {...}, "geometry": {...}}}``.

    Uses out_sr=4326 to match how backups' geometry was originally captured
    (see paging.fetch_all_features), so the comparison in verify_restore is
    apples-to-apples.
    """
    live_by_oid = {}
    unique_oids = sorted(set(oids))
    for start in range(0, len(unique_oids), _QUERY_BATCH_SIZE):
        batch = unique_oids[start:start + _QUERY_BATCH_SIZE]
        where = f"OBJECTID IN ({','.join(str(oid) for oid in batch)})"
        feature_set = layer.query(
            where=where, out_fields="*", out_sr=4326, return_all_records=True
        )
        for feature in feature_set.features:
            raw: dict = _as_dict(feature)  # type: ignore[assignment]
            oid = raw.get("attributes", {}).get("OBJECTID")
            live_by_oid[oid] = raw
    return live_by_oid


def _lonlat(geometry) -> tuple:
    if not geometry:
        return (None, None)
    return (geometry.get("x"), geometry.get("y"))


def _wkid(geometry):
    if not geometry:
        return None
    spatial_reference = geometry.get("spatialReference") or {}
    return spatial_reference.get("wkid") or spatial_reference.get("latestWkid")


def verify_restore(
    layer,
    backup_entries: list[dict],
    geometry_tolerance_m: float = DEFAULT_GEOMETRY_TOLERANCE_M,
) -> list[dict]:
    """Diff each backup entry's expected state against the live feature.

    Args:
        layer: An AGOL FeatureLayer-like object with a .query method.
        backup_entries: Entries as written by backup.write_backup, each
            shaped {"oid": <int>, "attributes": {...}, "geometry": {...}}
            -- the PRE-EDIT state a rollback restored features back to.
        geometry_tolerance_m: Maximum acceptable geodesic distance (meters)
            between the backup's geometry and the live feature's before
            it's flagged as a mismatch.

    Returns:
        One result dict per backup entry, shaped:
        {
            "oid": <int>,
            "live_feature_found": <bool>,
            "verified": <bool>,  # True iff the live feature was found and
                                  # matches on both attributes and geometry
            "attribute_mismatches": {field: (expected, actual), ...},
            "geometry_mismatch_m": <float | None>,  # measured distance if
                                  # computable; None if either side has no
                                  # usable geometry to compare
        }
        Fields in fields.EXCLUDED_FIELDS (AGOL-managed: GlobalID, Creator,
        CreationDate, Editor, EditDate, Shape/SHAPE, OBJECTID) are never
        compared -- they're expected to differ (or be absent from the
        backup's restored payload) after any real write, same as
        rollback.py's own restored_attrs filtering.
    """
    if not backup_entries:
        return []

    live_by_oid = _fetch_live_by_oid(layer, [entry["oid"] for entry in backup_entries])

    results = []
    for entry in backup_entries:
        oid = entry["oid"]
        live = live_by_oid.get(oid)

        if live is None:
            results.append({
                "oid": oid,
                "live_feature_found": False,
                "verified": False,
                "attribute_mismatches": {},
                "geometry_mismatch_m": None,
            })
            continue

        backup_attrs = entry.get("attributes", {})
        live_attrs = live.get("attributes", {})
        attribute_mismatches = {
            field: (expected, live_attrs.get(field))
            for field, expected in backup_attrs.items()
            if field not in EXCLUDED_FIELDS and live_attrs.get(field) != expected
        }

        backup_geometry = entry.get("geometry")
        live_geometry = live.get("geometry")
        backup_lon, backup_lat = _lonlat(backup_geometry)
        live_lon, live_lat = _lonlat(live_geometry)
        backup_wkid = _wkid(backup_geometry)
        live_wkid = _wkid(live_geometry)

        if (
            backup_wkid is not None
            and live_wkid is not None
            and backup_wkid != live_wkid
        ):
            # Both sides are expected to be WGS84 (paging.fetch_all_features
            # and this module's own query both request out_sr=4326) -- a
            # mismatch here means that invariant broke, and raw x/y values
            # from two different spatial references are not comparable as
            # degrees. Flag it rather than feeding geodesic_distance numbers
            # that would silently produce a meaningless "distance".
            geometry_ok = False
            geometry_mismatch_m = None
        elif None in (backup_lon, backup_lat, live_lon, live_lat):
            # Nothing to compare (both missing) is fine; one-sided missing
            # geometry is a real mismatch, just not one expressible as a
            # distance.
            geometry_ok = backup_lon is None and live_lon is None
            geometry_mismatch_m = None
        else:
            geometry_mismatch_m = geodesic_distance(backup_lon, backup_lat, live_lon, live_lat)
            geometry_ok = geometry_mismatch_m <= geometry_tolerance_m

        results.append({
            "oid": oid,
            "live_feature_found": True,
            "verified": not attribute_mismatches and geometry_ok,
            "attribute_mismatches": attribute_mismatches,
            "geometry_mismatch_m": geometry_mismatch_m,
        })

    return results
