"""AGOL feature-shape helpers shared by every code path that walks raw
feature dicts: the normal conflation run, ``--auto-configure``'s threshold
calibration, and standalone ``--calibrate``.

No ``arcgis`` import here -- these functions consume/produce only plain
dicts, following the codebase's existing pure/AGOL-integration split.
"""


def simplify_feature(raw_feature: dict) -> dict:
    """Flatten an AGOL feature dict ({"attributes": {...}, "geometry": {...}})
    into a simplified dict with "lon"/"lat" keys plus all attributes flattened in.

    geometry.x/.y are WGS84 lon/lat: fetch_all_features queries with
    out_sr=4326, so every feature reaching this function is already
    reprojected regardless of the source layer's native spatial reference.
    """
    attrs = dict(raw_feature.get("attributes", {}))
    geom = raw_feature.get("geometry") or {}
    simplified = dict(attrs)
    simplified["lon"] = geom.get("x")
    simplified["lat"] = geom.get("y")
    return simplified


def has_point_geometry(simplified_feature: dict) -> bool:
    """True if a feature simplified by ``simplify_feature`` has a usable
    point location (lon and lat both non-None).

    False for features with no geometry at all, or a non-point geometry
    (line/polygon geometries have no "x"/"y" keys, so ``simplify_feature``
    leaves lon/lat as None for them). Used to filter such features out
    before they reach geodesic_distance, which raises on None inputs.
    """
    return simplified_feature["lon"] is not None and simplified_feature["lat"] is not None
