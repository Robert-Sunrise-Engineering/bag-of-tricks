"""Data Conflation Tool - CLI & Initialization.

Merges captured (source) layer data into authoritative (destination) layer,
producing a review file for manual verification before applying changes.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from arcgis.features import FeatureLayer
from arcgis.gis import GIS
from pyproj import CRS


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
CONFIG_LOCAL_PATH = SCRIPT_DIR / "config.local.json"
AGOL_URL = "https://www.arcgis.com/sharing/rest"
AUTH_TIMEOUT = 30


# ---------------------------------------------------------------------------
# CLI Argument Parsing
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    """Parse command-line arguments.

    Returns:
        argparse.Namespace with parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Data Conflation Tool - Merge captured data into an authoritative AGOL feature layer.",
        epilog=(
            "Examples:\n"
            "  python conflate.py --layer \"LayerName\"                          # Dry run\n"
            "  python conflate.py --layer \"LayerName\" --apply                  # Apply changes\n"
            "  python conflate.py --layer \"LayerName\" --apply --resume         # Resume from checkpoint\n"
            "  python conflate.py --layer \"LayerName\" --apply --no-resume      # Fresh apply, ignore checkpoint\n"
            "  python conflate.py --layer \"LayerName\" --restore                # Restore from backup\n"
            "  python conflate.py --layer \"LayerName\" --auto-open              # Dry run + open review\n"
            'python conflate.py --layer "LayerName" --apply --migrate-attachments  # Apply + attachments\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--layer",
        type=str,
        required=True,
        help='Name of the layer to conflate (used for file naming)',
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write changes to AGOL instead of dry run",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        default=False,
        help="Restore authoritative layer from backup",
    )
    parser.add_argument(
        "--auto-open",
        action="store_true",
        default=False,
        help="Open review GeoPackage after dry run",
    )
    parser.add_argument(
        "--migrate-attachments",
        action="store_true",
        default=False,
        help="Migrate attachments during apply",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Auto-resume from checkpoint (fail if no checkpoint exists)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        default=False,
        help="Start fresh, ignore any existing checkpoint",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Configuration Loading
# ---------------------------------------------------------------------------

def load_config():
    """Load and merge config.json with config.local.json.

    Returns:
        dict: Combined configuration containing matching thresholds, paths,
              AGOL credentials, and layer URLs.
    """
    # Load shared config
    if not CONFIG_PATH.exists():
        print(f"Required config file not found: {CONFIG_PATH.name}")
        sys.exit(1)
    try:
        with open(CONFIG_PATH, "r") as f:
            shared_config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Failed to parse {CONFIG_PATH.name}: {e}")
        sys.exit(1)

    # Load local config (credentials)
    if not CONFIG_LOCAL_PATH.exists():
        print(f"Required config file not found: {CONFIG_LOCAL_PATH.name}")
        sys.exit(1)
    try:
        with open(CONFIG_LOCAL_PATH, "r") as f:
            local_config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Failed to parse {CONFIG_LOCAL_PATH.name}: {e}")
        sys.exit(1)

    # Merge into combined config
    config = {}
    config["matching"] = shared_config.get("matching", {})
    config["paths"] = shared_config.get("paths", {})
    config["apply"] = shared_config.get("apply", {"batch_size": 50, "max_retries": 3})
    config["agol"] = local_config.get("agol", {})
    config["captured_layer_url"] = local_config.get("captured_layer_url")
    config["auth_layer_url"] = local_config.get("auth_layer_url")

    return config


# ---------------------------------------------------------------------------
# AGOL Authentication
# ---------------------------------------------------------------------------

def authenticate_agol(config):
    """Authenticate to ArcGIS Online.

    Args:
        config: Combined configuration dict containing agol.username and agol.password.

    Returns:
        GIS: Authenticated GIS object.
    """
    username = config["agol"].get("username")
    password = config["agol"].get("password")

    if not username or not password:
        print("Could not authenticate to AGOL: username or password missing from config")
        sys.exit(1)

    try:
        gis = GIS(
            url=AGOL_URL,
            username=username,
            password=password,
            timeout=AUTH_TIMEOUT,
        )
        return gis
    except Exception as e:
        print(f"Could not authenticate to AGOL: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Layer Metadata Retrieval
# ---------------------------------------------------------------------------

def get_layer_info(gis, layer_url):
    """Retrieve metadata for an AGOL feature layer.

    Args:
        gis: Authenticated GIS object.
        layer_url: Full URL to the layer's FeatureServer/0 endpoint.

    Returns:
        dict with keys: layer_name, object_id_field, global_id_field,
        fields, has_attachments, use_global_ids, geometry_type.
    """
    try:
        layer = FeatureLayer(url=layer_url, gis=gis)
    except Exception as e:
        print(f"Layer not found or not accessible: {layer_url}")
        print(f"Error: {e}")
        sys.exit(1)

    props = layer.properties

    # Identify object ID field
    object_id_field = props.fields[0].name if props.fields else None

    # Identify global ID field
    global_id_field = None
    for field in props.fields:
        if field.get("globalId"):
            global_id_field = field.name
            break

    # Build fields list
    fields = []
    for field in props.fields:
        field_info = {
            "name": field.name,
            "type": field.type,
        }
        if "length" in field:
            field_info["length"] = field.length
        fields.append(field_info)

    # Determine if layer supports client-supplied GlobalIDs
    use_global_ids = global_id_field is not None

    return {
        "layer_name": props.name,
        "object_id_field": object_id_field,
        "global_id_field": global_id_field,
        "fields": fields,
        "has_attachments": bool(props.hasAttachments),
        "use_global_ids": use_global_ids,
        "geometry_type": props.geometryType,
    }


# ---------------------------------------------------------------------------
# Path Resolution
# ---------------------------------------------------------------------------

def resolve_paths(config, layer_name):
    """Resolve file paths for a conflation run.

    Args:
        config: Combined configuration dict.
        layer_name: Name of the layer being processed.

    Returns:
        dict with keys: backup_dir, backup_file, checkpoint_file,
        review_file, report_file.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    backup_base = config["paths"].get("backup", "backup/")
    reports_base = config["paths"].get("reports", "reports/")

    # Ensure paths end with /
    if not backup_base.endswith("/") and not backup_base.endswith("\\"):
        backup_base += "/"
    if not reports_base.endswith("/") and not reports_base.endswith("\\"):
        reports_base += "/"

    backup_dir = os.path.join(SCRIPT_DIR, backup_base)
    backup_file = os.path.join(backup_dir, f"{layer_name}_backup_{timestamp}.gpkg")
    checkpoint_file = os.path.join(
        backup_dir, f"{layer_name}_checkpoint_{timestamp}.json"
    )
    review_file = os.path.join(backup_dir, f"{layer_name}_conflation_review.gpkg")
    report_file = os.path.join(reports_base, f"{layer_name}_{timestamp}.csv")

    return {
        "backup_dir": backup_dir,
        "backup_file": backup_file,
        "checkpoint_file": checkpoint_file,
        "review_file": review_file,
        "report_file": report_file,
    }


# ---------------------------------------------------------------------------
# Data Loading & CRS Handling
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def load_layer_as_gdf(gis, layer_url, layer_info):
    """Load an AGOL feature layer as a GeoDataFrame.

    Args:
        gis: Authenticated GIS object.
        layer_url: Full URL to the layer's FeatureServer/0 endpoint.
        layer_info: Dict with layer metadata (keys: layer_name, fields).

    Returns:
        tuple: (geopandas.GeoDataFrame, skipped_count)
            Null/empty geometries are skipped. Empty layers return an empty
            GeoDataFrame with preserved schema. Output is in WGS 84 (EPSG:4326).
    """
    layer_name = layer_info["layer_name"]
    feature_layer = FeatureLayer(url=layer_url, gis=gis)

    # Query all features (without as_df to avoid spatial accessor issues)
    try:
        feature_set = feature_layer.query(as_df=False)
    except Exception as e:
        logger.error(f"Failed to query layer {layer_name}: {e}")
        raise

    # Convert FeatureSet to GeoDataFrame
    if not feature_set.features:
        logger.warning(f"Layer {layer_name} has no features with valid geometry")
        empty_gdf = gpd.GeoDataFrame(geometry=[])
        empty_gdf = empty_gdf.set_crs("EPSG:4326")
        return empty_gdf, 0

    # Detect source CRS from first feature's spatialReference
    first_geom = feature_set.features[0].geometry
    source_crs = None
    if first_geom and "spatialReference" in first_geom:
        sr = first_geom["spatialReference"]
        wkid = sr.get("wkid") or sr.get("latestWkid")
        if wkid:
            source_crs = f"EPSG:{wkid}"

    # Build records list with geometry handling
    records = []
    skipped_count = 0

    for feature in feature_set.features:
        geom = feature.geometry

        if geom is None:
            oid_val = feature.attributes.get("OBJECTID", "unknown")
            logger.warning(f"Skipping record OBJECTID={oid_val}: null/empty geometry")
            skipped_count += 1
            continue

        # Check if geometry is empty (no x/y or empty coords)
        if not isinstance(geom, dict):
            try:
                if hasattr(geom, "is_empty") and geom.is_empty:
                    oid_val = feature.attributes.get("OBJECTID", "unknown")
                    logger.warning(f"Skipping record OBJECTID={oid_val}: null/empty geometry")
                    skipped_count += 1
                    continue
            except Exception:
                pass
            records.append({**feature.attributes, "Shape": geom})
            continue

        x = geom.get("x")
        y = geom.get("y")
        if x is None or y is None:
            oid_val = feature.attributes.get("OBJECTID", "unknown")
            logger.warning(f"Skipping record OBJECTID={oid_val}: null/empty geometry")
            skipped_count += 1
            continue

        from shapely.geometry import Point
        records.append({**feature.attributes, "Shape": Point(x, y)})

    if not records:
        logger.warning(f"Layer {layer_name} has no features with valid geometry")
        empty_gdf = gpd.GeoDataFrame(geometry=[])
        empty_gdf = empty_gdf.set_crs("EPSG:4326")
        return empty_gdf, skipped_count

    # Create GeoDataFrame with detected source CRS
    gdf = gpd.GeoDataFrame(records, geometry="Shape")
    if source_crs:
        gdf = gdf.set_crs(source_crs)
    else:
        gdf = gdf.set_crs("EPSG:4326")

    # Reproject to WGS 84 if not already
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    return gdf, skipped_count


def pd_dtype_empty(series):
    """Return an empty array-like with the same dtype as the series."""
    if series.dtype.name.startswith("datetime"):
        return pd.to_datetime([])
    elif series.dtype.name == "int64" or series.dtype.name == "int32":
        return pd.Series([], dtype=series.dtype)
    elif series.dtype.name == "float64" or series.dtype.name == "float32":
        return pd.Series([], dtype=series.dtype)
    else:
        return pd.Series([], dtype="object")


def detect_utm_zone(gdf):
    """Detect the UTM zone EPSG code from a GeoDataFrame's centroid.

    Args:
        gdf: GeoDataFrame with geometries in WGS 84 (EPSG:4326).

    Returns:
        int: EPSG code for the UTM zone (326xx for northern, 327xx for southern).
    """
    centroid = gdf.geometry.union_all().centroid
    centroid_lon = centroid.x
    centroid_lat = centroid.y

    zone = int(np.floor((centroid_lon + 180) / 6)) + 1

    if centroid_lat >= 0:
        epsg = 32600 + zone
    else:
        epsg = 32700 + zone

    return epsg


def reproject_to_utm(gdf, epsg_code):
    """Reproject a GeoDataFrame to UTM.

    Args:
        gdf: GeoDataFrame in WGS 84 (EPSG:4326).
        epsg_code: Target UTM EPSG code.

    Returns:
        GeoDataFrame reprojected to the target CRS.
        If empty, returns the GeoDataFrame unchanged.
    """
    if gdf.empty:
        return gdf

    target_crs = CRS.from_epsg(epsg_code)
    return gdf.to_crs(target_crs)


def prepare_data(gis, captured_url, auth_url, captured_info, auth_info):
    """Load both layers and prepare for spatial processing.

    Loads captured and authoritative layers as GeoDataFrames, detects
    the UTM zone from the authoritative layer's centroid, and creates
    transient UTM-reprojected copies for spatial indexing and distance
    calculations.

    Args:
        gis: Authenticated GIS object.
        captured_url: URL of the captured (source) layer.
        auth_url: URL of the authoritative (destination) layer.
        captured_info: Dict with captured layer metadata.
        auth_info: Dict with authoritative layer metadata.

    Returns:
        dict with keys:
            captured_wgs84 — GeoDataFrame in WGS 84 (for output)
            auth_wgs84 — GeoDataFrame in WGS 84 (for output)
            captured_utm — GeoDataFrame in UTM (for spatial work)
            auth_utm — GeoDataFrame in UTM (for spatial work)
            utm_epsg — the detected EPSG code
    """
    # Load both layers in WGS 84
    captured_wgs84, captured_skipped = load_layer_as_gdf(gis, captured_url, captured_info)
    auth_wgs84, auth_skipped = load_layer_as_gdf(gis, auth_url, auth_info)

    logger.info(
        f"Loaded {len(captured_wgs84)} features from {captured_info['layer_name']}"
    )
    logger.info(
        f"Loaded {len(auth_wgs84)} features from {auth_info['layer_name']}"
    )
    if captured_skipped > 0:
        logger.warning(
            f"Skipped {captured_skipped} records with null/empty geometry from {captured_info['layer_name']}"
        )
    if auth_skipped > 0:
        logger.warning(
            f"Skipped {auth_skipped} records with null/empty geometry from {auth_info['layer_name']}"
        )

    # Detect UTM zone from authoritative layer
    if auth_wgs84.empty:
        # Fallback: use captured layer if auth is empty
        if not captured_wgs84.empty:
            utm_epsg = detect_utm_zone(captured_wgs84)
        else:
            # Both empty — default to a reasonable EPSG
            utm_epsg = 32618  # UTM 18N (New York default)
    else:
        utm_epsg = detect_utm_zone(auth_wgs84)

    logger.info(f"Detected UTM zone: EPSG:{utm_epsg}")

    # Create UTM-reprojected copies
    captured_utm = reproject_to_utm(captured_wgs84, utm_epsg)
    auth_utm = reproject_to_utm(auth_wgs84, utm_epsg)

    return {
        "captured_wgs84": captured_wgs84,
        "auth_wgs84": auth_wgs84,
        "captured_utm": captured_utm,
        "auth_utm": auth_utm,
        "utm_epsg": utm_epsg,
    }

def validate_schema(gdf_auth, layer_info):
    """Validate the authoritative layer schema before processing.

    Checks that the required 'COMMENTNOTES' field exists and determines its
    constraints (max length for text fields, type for non-text fields).

    Args:
        gdf_auth: GeoDataFrame of the authoritative layer (in WGS 84).
        layer_info: Dict with layer metadata (keys: layer_name, fields).

    Returns:
        dict with keys:
            valid — always True if no exception raised
            notes_max_length — max character length for text fields, or None
            field_types — dict mapping field names to their types

    Raises:
        SystemExit(1): If the 'COMMENTNOTES' field is missing from the layer.
    """
    layer_name = layer_info["layer_name"]

    if "COMMENTNOTES" not in gdf_auth.columns:
        print(f"FATAL: Authoritative layer '{layer_name}' is missing the required 'COMMENTNOTES' field. Aborting.")
        sys.exit(1)

    # Build field_types mapping from layer_info
    field_types = {}
    for field in layer_info.get("fields", []):
        field_types[field["name"]] = field["type"]

    # Determine COMMENTNOTES field constraints
    notes_max_length = None
    notes_field = None
    for field in layer_info.get("fields", []):
        if field["name"] == "COMMENTNOTES":
            notes_field = field
            break

    if notes_field is not None:
        type_val = notes_field["type"]
        # Handle both arcgis format (esriFieldTypeString) and plain format (String)
        is_text_or_numeric = (
            type_val in ("String", "text", "TEXT") or
            type_val.endswith("String") or
            type_val in ("SmallInteger", "Integer", "BigInt") or
            type_val in ("Double", "Float") or
            type_val.endswith("Integer") or
            type_val.endswith("Double") or
            type_val.endswith("Float")
        )
        if is_text_or_numeric:
            length = notes_field.get("length")
            if length is not None:
                notes_max_length = length

    return {
        "valid": True,
        "notes_max_length": notes_max_length,
        "field_types": field_types,
    }


def create_backup(gdf_auth, backup_path, layer_name):
    """Create a GeoPackage backup of the authoritative layer.

    Args:
        gdf_auth: GeoDataFrame of the authoritative layer (in WGS 84).
        backup_path: Full path for the output GeoPackage file.
        layer_name: Name of the layer (for logging).

    Raises:
        SystemExit(1): If backup creation fails.
    """
    backup_dir = os.path.dirname(backup_path)
    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)

    try:
        gdf_auth.to_file(backup_path, driver="GPKG")
        print(f"Backup created: {backup_path}")
    except Exception as e:
        print(f"FATAL: Backup failed: {e}")
        sys.exit(1)


def build_spatial_index(gdf):
    """Build an R-tree spatial index on a UTM-reprojected GeoDataFrame.

    Args:
        gdf: GeoDataFrame in UTM coordinates (meters).

    Returns:
        SpatialIndex object from shapely/pygeos, or None if the GeoDataFrame
        is empty.
    """
    if gdf.empty:
        return None
    return gdf.sindex


def calculate_distance_ft(geom_a, geom_b):
    """Calculate Euclidean distance between two UTM geometries in feet.

    Args:
        geom_a: First geometry (UTM, meters).
        geom_b: Second geometry (UTM, meters).

    Returns:
        Distance in feet (float).
    """
    distance_meters = geom_a.distance(geom_b)
    return distance_meters * 3.28084


def match_points(captured_utm, auth_utm, spatial_index, threshold_ft, ambiguity_pct):
    """Match each captured point to the nearest authoritative point(s).

    Classifies each match as "clean", "ambiguous", or "new" based on distance
    thresholds and ambiguity factor.

    Args:
        captured_utm: GeoDataFrame of captured points in UTM coordinates.
        auth_utm: GeoDataFrame of authoritative points in UTM coordinates.
        spatial_index: R-tree spatial index built from auth_utm.
        threshold_ft: Maximum distance in feet for a potential match.
        ambiguity_pct: Percentage factor for ambiguity detection.

    Returns:
        List of match result dicts, one per captured point.
    """
    if captured_utm.empty:
        return []

    # If no authoritative points, all captured points are "new"
    if auth_utm.empty or spatial_index is None:
        results = []
        for _, captured_row in captured_utm.iterrows():
            captured_oid = captured_row.get("OBJECTID")
            logger.info(
                f"New OBJECTID {captured_oid}: no match within {threshold_ft} ft (nearest: N/A)"
            )
            results.append({
                "captured_objectid": captured_oid,
                "auth_globalid": None,
                "auth_objectid": None,
                "distance_ft": None,
                "match_type": "new",
                "d1": None,
                "d2": None,
                "captured_geom_wgs84": captured_utm.loc[
                    captured_row.name, "captured_wgs84_geom"
                ] if "captured_wgs84_geom" in captured_utm.columns else captured_row.geometry,
                "auth_geom_wgs84": None,
            })
        return results

    ambiguity_factor = 1 + (ambiguity_pct / 100)
    results = []
    pos = 0

    for _, captured_row in captured_utm.iterrows():
        captured_oid = captured_row.get("OBJECTID")
        captured_geom = captured_utm.geometry.iloc[pos]

        # Query all authoritative points within a large distance, sorted by distance
        # Use a large distance to ensure we get all candidates
        all_indices = spatial_index.query(
            captured_geom, predicate="dwithin", distance=10000000, sort=True
        )

        if len(all_indices) == 0:
            # No neighbors found
            logger.info(
                f"New OBJECTID {captured_oid}: no match within {threshold_ft} ft (nearest: N/A)"
            )
            results.append({
                "captured_objectid": captured_oid,
                "auth_globalid": None,
                "auth_objectid": None,
                "distance_ft": None,
                "match_type": "new",
                "d1": None,
                "d2": None,
                "captured_geom_wgs84": captured_utm.geometry.iloc[pos],
                "auth_geom_wgs84": None,
            })
            continue

        # Calculate distances and sort by distance (nearest first)
        dist_pairs = []
        for idx in all_indices:
            dist_m = captured_geom.distance(auth_utm.geometry.iloc[idx])
            dist_pairs.append((int(idx), float(dist_m)))
        dist_pairs.sort(key=lambda x: x[1])

        # d1 = distance to nearest neighbor (feet)
        d1_ft = dist_pairs[0][1] * 3.28084

        # d2 = distance to second neighbor (feet), or infinity
        if len(dist_pairs) >= 2:
            d2_ft = dist_pairs[1][1] * 3.28084
        else:
            d2_ft = float("inf")

        # Get auth point info for nearest neighbor
        nearest_idx = dist_pairs[0][0]
        nearest_auth = auth_utm.iloc[nearest_idx]

        # Find the corresponding WGS84 auth geometry
        # We need the original WGS84 geometry — stored in auth_wgs84
        # The auth_utm index corresponds to auth_wgs84 index at the same position
        auth_globalid = nearest_auth.get("auth_globalid")
        auth_objectid = nearest_auth.get("auth_objectid")
        auth_geom_wgs84 = nearest_auth.get("auth_wgs84_geom")

        # Classification logic
        # Threshold is exclusive: exactly at threshold → "new"
        if d1_ft >= threshold_ft:
            match_type = "new"
            auth_globalid = None
            auth_objectid = None
            auth_geom_wgs84 = None
            logger.info(
                f"New OBJECTID {captured_oid}: no match within {threshold_ft} ft "
                f"(nearest: {d1_ft:.1f} ft)"
            )
        elif d2_ft > threshold_ft:
            # d1 within threshold, d2 beyond threshold → clean
            match_type = "clean"
            logger.info(
                f"Matched OBJECTID {captured_oid}: clean (d1={d1_ft:.1f} ft, d2={d2_ft:.1f} ft)"
            )
        elif d2_ft > d1_ft * ambiguity_factor:
            # d2 within threshold but significantly farther than d1 → clean
            match_type = "clean"
            logger.info(
                f"Matched OBJECTID {captured_oid}: clean (d1={d1_ft:.1f} ft, d2={d2_ft:.1f} ft)"
            )
        else:
            # d2 within threshold and within ambiguity factor → ambiguous
            match_type = "ambiguous"
            logger.info(
                f"Matched OBJECTID {captured_oid}: ambiguous (d1={d1_ft:.1f} ft, d2={d2_ft:.1f} ft)"
            )

        results.append({
            "captured_objectid": captured_oid,
            "auth_globalid": auth_globalid,
            "auth_objectid": auth_objectid,
            "distance_ft": d1_ft if match_type != "new" else None,
            "match_type": match_type,
            "d1": d1_ft,
            "d2": d2_ft if d2_ft != float("inf") else None,
            "captured_geom_wgs84": captured_utm.geometry.iloc[pos],
            "auth_geom_wgs84": auth_geom_wgs84,
        })
        pos += 1

    return results


def detect_collisions(match_results):
    """Detect cases where multiple captured points claim the same authoritative point.

    Groups match results by auth_globalid and identifies many-to-one conflicts
    where multiple captured points matched to the same authoritative record.

    Args:
        match_results: List of match result dicts from match_points().

    Returns:
        Dict mapping auth_globalid to list of conflicting match result dicts.
        Empty dict if no collisions detected.
    """
    # Group matched results by auth_globalid
    auth_groups = {}
    for result in match_results:
        if result["match_type"] == "new":
            continue
        auth_gid = result.get("auth_globalid")
        if auth_gid is None:
            continue
        if auth_gid not in auth_groups:
            auth_groups[auth_gid] = []
        auth_groups[auth_gid].append(result)

    # Filter to only groups with multiple claimants (actual collisions)
    collisions = {
        auth_gid: results
        for auth_gid, results in auth_groups.items()
        if len(results) > 1
    }

    return collisions


def resolve_collisions(match_results, collisions):
    """Resolve many-to-one collisions by keeping the closest captured point.

    For each collision group, the captured point with the smallest distance_ft
    wins. All other captured points are reclassified as "new".

    Args:
        match_results: List of match result dicts from match_points().
        collisions: Dict mapping auth_globalid to list of conflicting results.

    Returns:
        Updated list of match results with collisions resolved.
    """
    # Build a lookup from captured_objectid to result index
    oid_to_idx = {}
    for i, result in enumerate(match_results):
        oid_to_idx[result["captured_objectid"]] = i

    for auth_gid, claimants in collisions.items():
        # Log the collision
        claimant_strs = []
        for c in claimants:
            claimant_strs.append(
                f"captured OBJECTID {c['captured_objectid']} (d={c['distance_ft']:.1f} ft)"
            )
        logger.info(
            f"Collision detected: auth GlobalID {auth_gid} claimed by "
            f"{' and '.join(claimant_strs)}"
        )

        # Sort by distance_ft ascending; tie-break by lower captured_objectid
        claimants.sort(key=lambda r: (r["distance_ft"] if r["distance_ft"] is not None else float("inf"), r["captured_objectid"]))

        winner = claimants[0]
        winners = claimants[1:]

        # Log winner
        logger.info(
            f"  -> OBJECTID {winner['captured_objectid']} retains match (closest)"
        )

        # Add collision metadata to winner
        winner["collision_wins"] = len(claimants)
        winner["collision_resolved"] = True

        # Reclassify losers as "new"
        for loser in winners:
            loser["match_type"] = "new"
            loser["auth_globalid"] = None
            loser["auth_objectid"] = None
            loser["auth_geom_wgs84"] = None
            loser["distance_ft"] = None
            loser["collision_distance_ft"] = loser.get("d1")

            logger.info(
                f"  -> OBJECTID {loser['captured_objectid']} reclassified as new"
            )

    return match_results


def get_attachments_for_record(gis, layer_url, object_id):
    """Query AGOL for attachments on a specific record.

    Uses the ArcGIS API for Python's attachments interface to list
    attachments for a given OBJECTID.

    Args:
        gis: Authenticated GIS object.
        layer_url: Full URL to the layer's FeatureServer/0 endpoint.
        object_id: OBJECTID of the record to query attachments for.

    Returns:
        List of dicts with keys: id, name, size, contentType.
        Empty list if no attachments or on error.
    """
    from arcgis.features import FeatureLayer

    try:
        layer = FeatureLayer(url=layer_url, gis=gis)
        attachments = layer.attachments.get_list(object_id)
        results = []
        for att in attachments:
            results.append({
                "id": att.get("id"),
                "name": att.get("name", "unknown"),
                "size": att.get("size", 0),
                "contentType": att.get("contentType", "application/octet-stream"),
            })
        return results
    except Exception as e:
        logger.warning(f"Failed to query attachments for OBJECTID {object_id}: {e}")
        return []


def build_current_state_gdf(auth_wgs84):
    """Build a GeoDataFrame representing the current state of the authoritative layer.

    This is an exact copy of the authoritative GeoDataFrame with all fields preserved.

    Args:
        auth_wgs84: GeoDataFrame of the authoritative layer in WGS 84.

    Returns:
        GeoDataFrame — exact copy of auth_wgs84.
    """
    return auth_wgs84.copy()


def build_proposed_updates_gdf(match_results, captured_wgs84, auth_wgs84, threshold_ft):
    """Build a GeoDataFrame for the proposed_updates table.

    Contains one row per matched record (clean or ambiguous) with:
    - All captured layer attributes
    - Dual geometry columns (old_geometry, new_geometry)
    - Metadata columns (distance_ft, match_type, action, captured_objectid, label)

    Args:
        match_results: List of match result dicts from match_points/collision resolution.
        captured_wgs84: GeoDataFrame of captured layer in WGS 84.
        auth_wgs84: GeoDataFrame of authoritative layer in WGS 84.
        threshold_ft: Distance threshold in feet.

    Returns:
        GeoDataFrame with proposed updates.
    """
    import pandas as pd

    rows = []
    for result in match_results:
        if result["match_type"] not in ("clean", "ambiguous"):
            continue

        captured_oid = result["captured_objectid"]
        # Look up captured record by OBJECTID
        captured_row = captured_wgs84[captured_wgs84["OBJECTID"] == captured_oid]
        if captured_row.empty:
            continue
        captured_attrs = captured_row.iloc[0].to_dict()
        # Normalize geometry column name
        geom_col_name = captured_row.geometry.name
        if geom_col_name != "geometry":
            captured_attrs["geometry"] = captured_attrs.pop(geom_col_name)

        # Get auth geometry for old_geometry
        auth_gid = result.get("auth_globalid")
        if auth_gid is not None and "GlobalID" in auth_wgs84.columns:
            auth_geom_row = auth_wgs84[auth_wgs84["GlobalID"] == auth_gid]
            if not auth_geom_row.empty:
                old_geom = auth_geom_row.geometry.iloc[0]
            else:
                old_geom = result.get("auth_geom_wgs84")
        else:
            old_geom = result.get("auth_geom_wgs84")

        new_geom = result.get("captured_geom_wgs84")

        row = {
            **captured_attrs,
            "old_geometry": old_geom,
            "new_geometry": new_geom,
            "distance_ft": result["distance_ft"],
            "match_type": result["match_type"],
            "action": "updated",
            "captured_objectid": captured_oid,
            "label": f"Updated: {result['distance_ft']:.1f} ft from {result['match_type']}",
        }
        rows.append(row)

    if not rows:
        gdf = gpd.GeoDataFrame(
            {
                "OBJECTID": pd.Series([], dtype="int64"),
                "GlobalID": pd.Series([], dtype="object"),
                "COMMENTNOTES": pd.Series([], dtype="object"),
                "old_geometry": pd.Series([], dtype="object"),
                "new_geometry": pd.Series([], dtype="object"),
                "distance_ft": pd.Series([], dtype="float64"),
                "match_type": pd.Series([], dtype="object"),
                "action": pd.Series([], dtype="object"),
                "captured_objectid": pd.Series([], dtype="int64"),
                "label": pd.Series([], dtype="object"),
            },
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
        )
        return gdf

    result_gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    # Convert any geometry columns to WKT strings so they're regular columns
    for col in list(result_gdf.columns):
        if isinstance(result_gdf[col], gpd.GeoSeries):
            result_gdf[col] = result_gdf[col].to_wkt()
    result_gdf = result_gdf.set_geometry(gpd.GeoSeries([], crs="EPSG:4326"))
    return result_gdf


def build_proposed_new_gdf(match_results, captured_wgs84, captured_info, auth_info, threshold_ft):
    """Build a GeoDataFrame for the proposed_new table.

    Contains one row per unmatched record (new) with:
    - All captured layer attributes
    - Concatenated notes from non-matching captured fields
    - Metadata columns (match_type, action, captured_objectid, label)

    Notes concatenation: for each field that exists in auth_info["fields"]
    and has a non-null value in the captured record, format as
    "FieldName: value", joined by " | ".

    Args:
        match_results: List of match result dicts.
        captured_wgs84: GeoDataFrame of captured layer in WGS 84.
        captured_info: Dict with captured layer metadata (keys: fields).
        auth_info: Dict with authoritative layer metadata (keys: fields).
        threshold_ft: Distance threshold in feet.

    Returns:
        GeoDataFrame with proposed new records.
    """
    # Build set of auth field names for notes concatenation
    auth_field_names = set()
    for field in auth_info.get("fields", []):
        auth_field_names.add(field["name"])

    # Get captured field names (in order)
    captured_field_names = []
    for field in captured_info.get("fields", []):
        captured_field_names.append(field["name"])

    rows = []
    for result in match_results:
        if result["match_type"] != "new":
            continue

        captured_oid = result["captured_objectid"]
        captured_row = captured_wgs84[captured_wgs84["OBJECTID"] == captured_oid]
        if captured_row.empty:
            continue
        captured_attrs = captured_row.iloc[0].to_dict()
        # Normalize geometry column name
        geom_col_name = captured_row.geometry.name
        if geom_col_name != "geometry":
            captured_attrs["geometry"] = captured_attrs.pop(geom_col_name)

        # Build notes from captured fields that exist in auth schema
        notes_parts = []
        for field_name in captured_field_names:
            if field_name in auth_field_names:
                value = captured_attrs.get(field_name)
                if value is not None and pd.notna(value):
                    notes_parts.append(f"{field_name}: {value}")

        notes = " | ".join(notes_parts) if notes_parts else None

        row = {
            **captured_attrs,
            "distance_ft": None,
            "match_type": "new",
            "action": "appended",
            "captured_objectid": captured_oid,
            "label": f"New: no match within {threshold_ft} ft",
        }
        # Override COMMENTNOTES with concatenated notes if we have any
        if notes is not None:
            row["COMMENTNOTES"] = notes

        rows.append(row)

    if not rows:
        gdf = gpd.GeoDataFrame(
            {
                "OBJECTID": pd.Series([], dtype="int64"),
                "GlobalID": pd.Series([], dtype="object"),
                "COMMENTNOTES": pd.Series([], dtype="object"),
                "distance_ft": pd.Series([], dtype="float64"),
                "match_type": pd.Series([], dtype="object"),
                "action": pd.Series([], dtype="object"),
                "captured_objectid": pd.Series([], dtype="int64"),
                "label": pd.Series([], dtype="object"),
            },
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
        )
        return gdf

    result_gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    result_gdf = result_gdf.set_geometry("geometry")
    return result_gdf


def build_proposed_attachments_gdf(match_results, gis, captured_url):
    """Build a GeoDataFrame for the proposed_attachments table.

    Queries AGOL for attachments on matched records (clean or ambiguous).
    In dry run mode, only metadata is collected (no download).

    Args:
        match_results: List of match result dicts.
        gis: Authenticated GIS object.
        captured_url: URL of the captured layer's FeatureServer/0 endpoint.

    Returns:
        GeoDataFrame with attachment metadata.
    """
    rows = []
    for result in match_results:
        if result["match_type"] not in ("clean", "ambiguous"):
            continue

        captured_oid = result["captured_objectid"]
        auth_gid = result.get("auth_globalid")

        attachments = get_attachments_for_record(gis, captured_url, captured_oid)
        for att in attachments:
            rows.append({
                "captured_objectid": captured_oid,
                "auth_globalid": auth_gid,
                "attachment_name": att["name"],
                "attachment_size_bytes": att["size"],
                "attachment_type": att["contentType"],
                "status": "pending",
            })

    if not rows:
        gdf = gpd.GeoDataFrame(
            columns=[
                "captured_objectid", "auth_globalid",
                "attachment_name", "attachment_size_bytes",
                "attachment_type", "status",
            ]
        )
        return gdf

    result_gdf = gpd.GeoDataFrame(rows)
    return result_gdf


def write_review_geopackage(match_results, captured_wgs84, auth_wgs84,
                            captured_url, gis, review_path,
                            threshold_ft, auth_info, captured_info):
    """Write the dry run review GeoPackage with 4 tables.

    Creates a GeoPackage file containing:
    - current_state: exact copy of authoritative layer
    - proposed_updates: matched records with dual geometry
    - proposed_new: unmatched records with concatenated notes
    - proposed_attachments: attachment metadata from AGOL

    Args:
        match_results: List of match result dicts.
        captured_wgs84: GeoDataFrame of captured layer in WGS 84.
        auth_wgs84: GeoDataFrame of authoritative layer in WGS 84.
        captured_url: URL of the captured layer's FeatureServer/0 endpoint.
        gis: Authenticated GIS object.
        review_path: Full path for the output GeoPackage file.
        threshold_ft: Distance threshold in feet.
        auth_info: Dict with authoritative layer metadata.
        captured_info: Dict with captured layer metadata.
    """
    review_dir = os.path.dirname(review_path)
    if review_dir:
        os.makedirs(review_dir, exist_ok=True)

    # Build each table
    current_state = build_current_state_gdf(auth_wgs84)
    proposed_updates = build_proposed_updates_gdf(match_results, captured_wgs84, auth_wgs84, threshold_ft)
    proposed_new = build_proposed_new_gdf(match_results, captured_wgs84, captured_info, auth_info, threshold_ft)
    proposed_attachments = build_proposed_attachments_gdf(match_results, gis, captured_url)

    # Delete existing review file to avoid appending to stale data
    if os.path.exists(review_path):
        os.remove(review_path)

    # Write each table as a separate layer
    current_state.to_file(review_path, layer="current_state", driver="GPKG")
    proposed_updates.to_file(review_path, layer="proposed_updates", driver="GPKG", mode="a")
    proposed_new.to_file(review_path, layer="proposed_new", driver="GPKG", mode="a")
    proposed_attachments.to_file(review_path, layer="proposed_attachments", driver="GPKG", mode="a")

    print(f"Review file created: {review_path}")


def update_review_geopackage_attachments(review_path, match_results, captured_wgs84, auth_wgs84,
                                          captured_url, gis, threshold_ft, auth_info, captured_info,
                                          migration_results):
    """Rewrite the review GeoPackage with updated attachment migration statuses.

    Reads the existing proposed_attachments table, updates status column with
    actual migration results, then deletes and rewrites all 4 tables to avoid
    duplicate rows from mode="a" appending behavior.

    Args:
        review_path: Path to the review GeoPackage file.
        match_results: List of match result dicts.
        captured_wgs84: GeoDataFrame of captured layer in WGS 84.
        auth_wgs84: GeoDataFrame of authoritative layer in WGS 84.
        captured_url: URL of the captured layer's FeatureServer/0 endpoint.
        gis: Authenticated GIS object.
        threshold_ft: Distance threshold in feet.
        auth_info: Dict with authoritative layer metadata.
        captured_info: Dict with captured layer metadata.
        migration_results: List of migration result dicts from migrate_attachments().
    """
    # Read the existing proposed_attachments table
    existing_attachments = gpd.read_file(review_path, layer="proposed_attachments")

    # Ensure it's a GeoDataFrame (read_file may return DataFrame if no geometry column)
    if not isinstance(existing_attachments, gpd.GeoDataFrame):
        existing_attachments = gpd.GeoDataFrame(existing_attachments)

    # Update status column with actual migration results
    for mr in migration_results:
        mask = (
            (existing_attachments["captured_objectid"] == mr["captured_objectid"]) &
            (existing_attachments["attachment_name"] == mr["attachment_name"])
        )
        if mask.any():
            existing_attachments.loc[mask, "status"] = mr["status"]

    # Delete existing review file to avoid appending to stale data
    if os.path.exists(review_path):
        os.remove(review_path)

    # Rebuild all 4 tables and write fresh
    review_dir = os.path.dirname(review_path)
    if review_dir:
        os.makedirs(review_dir, exist_ok=True)

    current_state = build_current_state_gdf(auth_wgs84)
    proposed_updates = build_proposed_updates_gdf(match_results, captured_wgs84, auth_wgs84, threshold_ft)
    proposed_new = build_proposed_new_gdf(match_results, captured_wgs84, captured_info, auth_info, threshold_ft)

    current_state.to_file(review_path, layer="current_state", driver="GPKG")
    proposed_updates.to_file(review_path, layer="proposed_updates", driver="GPKG", mode="a")
    proposed_new.to_file(review_path, layer="proposed_new", driver="GPKG", mode="a")
    existing_attachments.to_file(review_path, layer="proposed_attachments", driver="GPKG", mode="a")


def write_report_csv(match_results, report_path, gis=None, captured_url=None):
    """Write the CSV report with one row per captured record.

    Columns: layer, captured_objectid, auth_globalid, distance_ft,
             match_type, action, attachment_count, attachment_names

    Args:
        match_results: List of match result dicts.
        report_path: Full path for the output CSV file.
        gis: Authenticated GIS object (optional, for attachment queries).
        captured_url: URL of the captured layer (optional, for attachment queries).
    """
    report_dir = os.path.dirname(report_path)
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)

    rows = []
    for result in match_results:
        action = "updated" if result["match_type"] in ("clean", "ambiguous") else "appended"

        # Query attachments for matched records
        attachment_count = 0
        attachment_names = ""
        if gis is not None and captured_url is not None and result["match_type"] in ("clean", "ambiguous"):
            attachments = get_attachments_for_record(gis, captured_url, result["captured_objectid"])
            attachment_count = len(attachments)
            if attachments:
                attachment_names = "; ".join(a["name"] for a in attachments)

        rows.append({
            "layer": "",
            "captured_objectid": result["captured_objectid"],
            "auth_globalid": result.get("auth_globalid") or "",
            "distance_ft": result.get("distance_ft") if result.get("distance_ft") is not None else "",
            "match_type": result["match_type"],
            "action": action,
            "attachment_count": attachment_count,
            "attachment_names": attachment_names,
        })

    df = pd.DataFrame(rows, columns=[
        "layer", "captured_objectid", "auth_globalid",
        "distance_ft", "match_type", "action",
        "attachment_count", "attachment_names",
    ])
    df.to_csv(report_path, index=False)
    print(f"Report written: {report_path}")


def count_total_attachments(match_results, gis, captured_url):
    """Count total attachments across all matched records.

    Args:
        match_results: List of match result dicts.
        gis: Authenticated GIS object.
        captured_url: URL of the captured layer.

    Returns:
        int: Total number of attachments.
    """
    total = 0
    for result in match_results:
        if result["match_type"] in ("clean", "ambiguous"):
            attachments = get_attachments_for_record(gis, captured_url, result["captured_objectid"])
            total += len(attachments)
    return total


def print_conflation_summary(match_results, attachment_count):
    """Print the conflation summary to console.

    Args:
        match_results: List of match result dicts.
        attachment_count: Total number of attachments.
    """
    clean = sum(1 for r in match_results if r["match_type"] == "clean")
    ambiguous = sum(1 for r in match_results if r["match_type"] == "ambiguous")
    new = sum(1 for r in match_results if r["match_type"] == "new")
    total = len(match_results)

    print()
    print("=== Conflation Summary ===")
    print(f"Matched (clean):     {clean}")
    print(f"Matched (ambiguous): {ambiguous}")
    print(f"New:                 {new}")
    print(f"Attachments pending: {attachment_count}")
    print(f"Total:               {total}")
    print()


def auto_open_review(review_path):
    """Open the review GeoPackage file.

    On Windows: uses os.startfile()
    On other platforms: uses subprocess with xdg-open

    Args:
        review_path: Full path to the review GeoPackage file.
    """
    try:
        if sys.platform == "win32":
            os.startfile(review_path)
        else:
            subprocess.run(["xdg-open", review_path], check=False)
    except Exception as e:
        print(f"Could not auto-open review file: {review_path}")
        print(f"Error: {e}")


def load_checkpoint(checkpoint_path):
    """Load a checkpoint file.

    Args:
        checkpoint_path: Path to the JSON checkpoint file.

    Returns:
        dict with checkpoint data, or None if file doesn't exist.
    """
    if not os.path.exists(checkpoint_path):
        return None
    try:
        with open(checkpoint_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_checkpoint(checkpoint_path, checkpoint_data):
    """Save checkpoint data to a JSON file.

    Args:
        checkpoint_path: Path to the JSON checkpoint file.
        checkpoint_data: Dict with checkpoint data to save.
    """
    checkpoint_dir = os.path.dirname(checkpoint_path)
    if checkpoint_dir:
        os.makedirs(checkpoint_dir, exist_ok=True)
    with open(checkpoint_path, "w") as f:
        json.dump(checkpoint_data, f, indent=2)


def checkpoint_add_update(checkpoint_path, global_id):
    """Append a GlobalID to the applied_updates list in a checkpoint file.

    Creates the checkpoint file if it doesn't exist.

    Args:
        checkpoint_path: Path to the JSON checkpoint file.
        global_id: GlobalID string to append.
    """
    data = load_checkpoint(checkpoint_path)
    if data is None:
        data = {
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "layer": "",
            "applied_updates": [],
            "applied_new": [],
        }
    if "applied_updates" not in data:
        data["applied_updates"] = []
    data["applied_updates"].append(global_id)
    save_checkpoint(checkpoint_path, data)


def checkpoint_add_new(checkpoint_path, global_id):
    """Append a GlobalID to the applied_new list in a checkpoint file.

    Creates the checkpoint file if it doesn't exist.

    Args:
        checkpoint_path: Path to the JSON checkpoint file.
        global_id: GlobalID string to append.
    """
    data = load_checkpoint(checkpoint_path)
    if data is None:
        data = {
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "layer": "",
            "applied_updates": [],
            "applied_new": [],
        }
    if "applied_new" not in data:
        data["applied_new"] = []
    data["applied_new"].append(global_id)
    save_checkpoint(checkpoint_path, data)


def manage_checkpoint(checkpoint_path, args):
    """Manage checkpoint lifecycle: create, resume, or ignore.

    Args:
        checkpoint_path: Path to the JSON checkpoint file.
        args: Parsed CLI arguments (namespace with --resume and --no-resume).

    Returns:
        dict with keys: applied_updates (list), applied_new (list).

    Raises:
        SystemExit(1): If --resume is set but no checkpoint exists.
    """
    existing = load_checkpoint(checkpoint_path)

    if existing is not None:
        if args.resume:
            print(f"Previous checkpoint found. Resuming from previous run.")
            print(f"Resuming: {len(existing.get('applied_updates', []))} updates and {len(existing.get('applied_new', []))} new records already applied")
            return existing
        elif args.no_resume:
            print("Starting fresh. Ignoring previous checkpoint.")
            os.remove(checkpoint_path)
            return {"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"), "layer": "", "applied_updates": [], "applied_new": []}
        else:
            response = input("Previous checkpoint found. Resume from previous run? [Y/n]: ")
            if response.lower() in ("y", ""):
                print(f"Resuming: {len(existing.get('applied_updates', []))} updates and {len(existing.get('applied_new', []))} new records already applied")
                return existing
            else:
                print("Starting fresh. Ignoring previous checkpoint.")
                os.remove(checkpoint_path)
                return {"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"), "layer": "", "applied_updates": [], "applied_new": []}
    else:
        if args.resume:
            print(f"No checkpoint found at {checkpoint_path}. Cannot resume.")
            sys.exit(1)
        new_checkpoint = {
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "layer": "",
            "applied_updates": [],
            "applied_new": [],
        }
        save_checkpoint(checkpoint_path, new_checkpoint)
        return new_checkpoint



def _to_native(value):
    """Convert pandas/numpy types to native Python types for AGOL API.

    Handles: numpy.int64/float64/bool_, pandas.NA, pd.NaT, NaN values.
    Returns None for any null/NaN-like value.

    Args:
        value: A value that may be a pandas/numpy type.

    Returns:
        Native Python value (str, int, float, bool, or None).
    """
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    # numpy types have .item() method to convert to native Python
    if hasattr(value, 'item'):
        return value.item()
    # pandas NA/NaT types
    if pd.isna(value):
        return None
    return value



def build_update_payload(captured_row, auth_row, auth_field_names, schema_result):
    """Build an update payload for a matched record.

    For each auth field: use captured value if non-null, else keep auth value.
    Notes field: append new concatenated notes to existing auth notes.
    All values are converted to native Python types via _to_native().

    Args:
        captured_row: Row from captured GeoDataFrame.
        auth_row: Row from authoritative GeoDataFrame.
        auth_field_names: List of field names in the authoritative layer.
        schema_result: Dict from validate_schema() with notes_max_length and field_types.

    Returns:
        dict with attribute updates and geometry.
    """
    notes_max_length = schema_result.get("notes_max_length")
    payload = {}

    # Fields that AGOL does not allow updating via edit_features
    READONLY_FIELDS = {"OBJECTID", "GlobalID", "CreationDate", "Creator", "EditDate", "Editor", "created_user", "created_date", "last_edited_user", "last_edited_date"}

    for field_name in auth_field_names:
        if field_name in READONLY_FIELDS:
            continue
        captured_val = captured_row.get(field_name)
        auth_val = auth_row.get(field_name) if auth_row is not None else None

        if field_name == "COMMENTNOTES":
            # Build notes from non-matching captured attributes
            notes_parts = []
            for fn in auth_field_names:
                if fn in ("OBJECTID", "GlobalID", "COMMENTNOTES"):
                    continue
                cv = captured_row.get(fn)
                if cv is not None and pd.notna(cv):
                    notes_parts.append(f"{fn}: {cv}")
            new_notes = " | ".join(notes_parts) if notes_parts else ""

            # Append to existing auth notes (ensure both are strings)
            existing_notes = str(auth_val) if auth_val is not None and str(auth_val).strip() != "" and str(auth_val) != "nan" else ""
            if existing_notes and new_notes:
                combined = f"{existing_notes} | {new_notes}"
            elif new_notes:
                combined = new_notes
            else:
                combined = existing_notes

            # Ensure combined is always a string
            combined = str(combined)

            if notes_max_length and len(combined) > notes_max_length:
                combined = combined[:notes_max_length]
                logger.warning(f"Notes truncated to {notes_max_length} chars")

            payload["COMMENTNOTES"] = combined
        else:
            # Convert to native Python type for AGOL API
            native_captured = _to_native(captured_val)
            native_auth = _to_native(auth_val)
            if native_captured is not None:
                payload[field_name] = native_captured
            elif native_auth is not None:
                payload[field_name] = native_auth

    return payload



def build_append_payload(captured_row, auth_field_names, schema_result):
    """Build an append payload for a new record.

    For each auth field: use captured value if non-null, skip if null.
    Notes field: concatenate non-matching captured attributes.
    All values are converted to native Python types via _to_native().

    Args:
        captured_row: Row from captured GeoDataFrame.
        auth_field_names: List of field names in the authoritative layer.
        schema_result: Dict from validate_schema() with notes_max_length and field_types.

    Returns:
        dict with attribute updates and geometry.
    """
    notes_max_length = schema_result.get("notes_max_length")
    payload = {}

    # Fields that AGOL does not allow updating via edit_features
    READONLY_FIELDS = {"OBJECTID", "GlobalID", "CreationDate", "Creator", "EditDate", "Editor", "created_user", "created_date", "last_edited_user", "last_edited_date"}

    for field_name in auth_field_names:
        if field_name in READONLY_FIELDS:
            continue
        captured_val = captured_row.get(field_name)

        if field_name == "COMMENTNOTES":
            notes_parts = []
            for fn in auth_field_names:
                if fn in ("OBJECTID", "GlobalID", "COMMENTNOTES"):
                    continue
                cv = captured_row.get(fn)
                if cv is not None and pd.notna(cv):
                    notes_parts.append(f"{fn}: {cv}")
            new_notes = " | ".join(notes_parts) if notes_parts else ""

            if notes_max_length and len(new_notes) > notes_max_length:
                new_notes = new_notes[:notes_max_length]
                logger.warning(f"Notes truncated to {notes_max_length} chars")

            payload["COMMENTNOTES"] = new_notes
        else:
            # Convert to native Python type for AGOL API
            native_val = _to_native(captured_val)
            if native_val is not None:
                payload[field_name] = native_val

    return payload



def _is_retryable_error(e):
    """Determine if an error is retryable (network/timeout) vs permanent (API error).

    Args:
        e: Exception from AGOL API call.

    Returns:
        bool: True if the error should be retried.
    """
    error_str = str(e).lower()
    retryable_keywords = [
        "timeout", "timed out", "connection", "network", "refused",
        "reset", "broken", "service unavailable", "502", "503", "504",
        "temporary", "retry",
    ]
    permanent_keywords = [
        "invalid field", "invalid type", "validation error", "feature not found",
        "update failed", "geometry", "sql", "errorcode",
    ]

    for keyword in permanent_keywords:
        if keyword in error_str:
            return False

    for keyword in retryable_keywords:
        if keyword in error_str:
            return True

    return True


def _apply_with_retry(func, max_retries, *args, **kwargs):
    """Apply a function with exponential backoff for retryable errors.

    Args:
        func: The function to call.
        max_retries: Maximum number of retries.
        *args: Positional arguments to pass to func.
        **kwargs: Keyword arguments to pass to func.

    Returns:
        The result of func on success.

    Raises:
        The last exception if all retries fail.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < max_retries and _is_retryable_error(e):
                delay = 2 ** attempt
                logger.info(f"Retryable error, retrying in {delay}s: {e}")
                time.sleep(delay)
            else:
                break
    raise last_error


def apply_updates_in_batches(auth_layer, match_results, checkpoint, schema_result, auth_wgs84, batch_size):
    """Apply updates to matched records in batches.

    Args:
        auth_layer: FeatureLayer object for the authoritative layer.
        match_results: List of match result dicts.
        checkpoint: Checkpoint dict with applied_updates and applied_new lists.
        schema_result: Dict from validate_schema().
        auth_wgs84: GeoDataFrame of authoritative layer in WGS 84.
        batch_size: Number of records per batch.

    Returns:
        list of dicts with keys: auth_globalid, error (for failed records).
    """
    from arcgis.features import FeatureLayer as FL

    auth_field_names = [f["name"] for f in auth_layer.properties.fields]
    max_retries = checkpoint.get("max_retries", 3)
    failed = []

    # Filter to matched records not yet applied
    matched = [r for r in match_results if r["match_type"] in ("clean", "ambiguous")
               and r["auth_globalid"] not in checkpoint["applied_updates"]]

    if not matched:
        print("No updates to apply.")
        return failed

    total = len(matched)
    batch_num = 0

    for i in range(0, len(matched), batch_size):
        batch = matched[i:i + batch_size]
        batch_num += 1
        print(f"Updating batch {batch_num}/{(total + batch_size - 1) // batch_size} ({len(batch)} records)...")

        updates = []
        for record in batch:
            auth_gid = record["auth_globalid"]
            captured_oid = record["captured_objectid"]

            # Find the auth row
            auth_row = None
            if auth_wgs84 is not None and not auth_wgs84.empty:
                row_match = auth_wgs84[auth_wgs84["GlobalID"] == auth_gid]
                if not row_match.empty:
                    auth_row = row_match.iloc[0]

            payload = build_update_payload(record, auth_row, auth_field_names, schema_result)
            payload["globalid"] = auth_gid

            if payload:
                updates.append(payload)

        if not updates:
            print(f"  Batch {batch_num}: no updates to apply")
            continue

        try:
            result = _apply_with_retry(
                auth_layer.edit_features,
                max_retries,
                updates=updates,
            )
            # Success: add to checkpoint
            for record in batch:
                if record["auth_globalid"] not in checkpoint["applied_updates"]:
                    checkpoint["applied_updates"].append(record["auth_globalid"])
            save_checkpoint(checkpoint.get("_path", ""), checkpoint)
            print(f"  Batch {batch_num}: {len(updates)} updated successfully")

        except Exception as e:
            print(f"  Batch {batch_num} failed: {e}. Falling back to one-at-a-time...")
            for record in batch:
                auth_gid = record["auth_globalid"]
                captured_oid = record["captured_objectid"]

                auth_row = None
                if auth_wgs84 is not None and not auth_wgs84.empty:
                    row_match = auth_wgs84[auth_wgs84["GlobalID"] == auth_gid]
                    if not row_match.empty:
                        auth_row = row_match.iloc[0]

                payload = build_update_payload(record, auth_row, auth_field_names, schema_result)
                payload["globalid"] = auth_gid

                if not payload:
                    checkpoint["applied_updates"].append(auth_gid)
                    save_checkpoint(checkpoint.get("_path", ""), checkpoint)
                    continue

                try:
                    _apply_with_retry(
                        auth_layer.edit_features,
                        max_retries,
                        updates=[payload],
                    )
                    checkpoint["applied_updates"].append(auth_gid)
                    save_checkpoint(checkpoint.get("_path", ""), checkpoint)
                    print(f"  Updated OBJECTID {captured_oid} (GlobalID {auth_gid})")
                except Exception as single_e:
                    print(f"  Failed to update OBJECTID {captured_oid} (GlobalID {auth_gid}): {single_e}")
                    failed.append({"auth_globalid": auth_gid, "error": str(single_e)})

    return failed


def apply_appends_in_batches(auth_layer, match_results, checkpoint, schema_result, batch_size, use_global_ids=False):
    """Append new records in batches.

    Args:
        auth_layer: FeatureLayer object for the authoritative layer.
        match_results: List of match result dicts.
        checkpoint: Checkpoint dict with applied_updates and applied_new lists.
        schema_result: Dict from validate_schema().
        batch_size: Number of records per batch.
        use_global_ids: Whether the layer supports client-supplied GlobalIDs.

    Returns:
        list of dicts with keys: captured_objectid, error (for failed records).
    """
    auth_field_names = [f["name"] for f in auth_layer.properties.fields]
    max_retries = checkpoint.get("max_retries", 3)
    failed = []

    # Filter to new records not yet applied
    new_records = [r for r in match_results if r["match_type"] == "new"
                   and r.get("_new_globalid") not in checkpoint["applied_new"]]

    if not new_records:
        print("No new records to append.")
        return failed

    total = len(new_records)
    batch_num = 0

    for i in range(0, len(new_records), batch_size):
        batch = new_records[i:i + batch_size]
        batch_num += 1
        print(f"Appending batch {batch_num}/{(total + batch_size - 1) // batch_size} ({len(batch)} records)...")

        appends = []
        for record in batch:
            captured_oid = record["captured_objectid"]
            captured_geom = record.get("captured_geom_wgs84")

            payload = build_append_payload(record, auth_field_names, schema_result)

            if use_global_ids:
                new_gid = str(uuid.uuid4())
                payload["GlobalID"] = new_gid
                record["_new_globalid"] = new_gid

            if payload and captured_geom is not None:
                appends.append({
                    "attributes": payload,
                    "geometry": captured_geom.__geo_interface__ if hasattr(captured_geom, '__geo_interface__') else captured_geom,
                })

        if not appends:
            print(f"  Batch {batch_num}: no appends to apply")
            continue

        try:
            result = _apply_with_retry(
                auth_layer.edit_features,
                max_retries,
                appends=appends,
            )
            for record in batch:
                new_gid = record.get("_new_globalid")
                if new_gid and new_gid not in checkpoint["applied_new"]:
                    checkpoint["applied_new"].append(new_gid)
            save_checkpoint(checkpoint.get("_path", ""), checkpoint)
            print(f"  Batch {batch_num}: {len(appends)} appended successfully")

        except Exception as e:
            print(f"  Batch {batch_num} failed: {e}. Falling back to one-at-a-time...")
            for record in batch:
                captured_oid = record["captured_objectid"]
                captured_geom = record.get("captured_geom_wgs84")

                payload = build_append_payload(record, auth_field_names, schema_result)

                if use_global_ids:
                    new_gid = str(uuid.uuid4())
                    payload["GlobalID"] = new_gid
                    record["_new_globalid"] = new_gid

                if not payload or captured_geom is None:
                    new_gid = record.get("_new_globalid")
                    if new_gid and new_gid not in checkpoint["applied_new"]:
                        checkpoint["applied_new"].append(new_gid)
                    save_checkpoint(checkpoint.get("_path", ""), checkpoint)
                    continue

                try:
                    _apply_with_retry(
                        auth_layer.edit_features,
                        max_retries,
                        appends=[{
                            "attributes": payload,
                            "geometry": captured_geom.__geo_interface__ if hasattr(captured_geom, '__geo_interface__') else captured_geom,
                        }],
                    )
                    new_gid = record.get("_new_globalid")
                    if new_gid and new_gid not in checkpoint["applied_new"]:
                        checkpoint["applied_new"].append(new_gid)
                    save_checkpoint(checkpoint.get("_path", ""), checkpoint)
                    print(f"  Appended new record — captured OBJECTID {captured_oid}")
                except Exception as single_e:
                    print(f"  Failed to append new record — captured OBJECTID {captured_oid}: {single_e}")
                    failed.append({"captured_objectid": captured_oid, "error": str(single_e)})

    return failed



def migrate_attachments(auth_layer, match_results, captured_url, gis, checkpoint):
    """Migrate attachments from captured records to their matched authoritative records.

    For each matched record (clean or ambiguous), queries attachments from the
    captured record, downloads them, and uploads to the authoritative record.
    Independent failures per attachment.

    Args:
        auth_layer: FeatureLayer object for the authoritative layer.
        match_results: List of match result dicts.
        captured_url: URL of the captured layer FeatureServer/0 endpoint.
        gis: Authenticated GIS object.
        checkpoint: Checkpoint dict with applied_updates and applied_new lists.

    Returns:
        list of dicts with keys: captured_objectid, auth_globalid,
        attachment_name, status, error (optional).
    """
    import tempfile

    migrated = 0
    skipped = 0
    failed = 0
    results = []

    for result in match_results:
        if result["match_type"] not in ("clean", "ambiguous"):
            continue

        captured_oid = result["captured_objectid"]
        auth_gid = result.get("auth_globalid")

        if auth_gid is None:
            continue

        # Check if already checkpointed
        if auth_gid in checkpoint.get("applied_updates", []):
            continue

        # Query attachments from captured record
        captured_layer = FeatureLayer(url=captured_url, gis=gis)
        try:
            attachments = captured_layer.attachments.get_list(captured_oid)
        except Exception as e:
            logger.warning(f"Failed to query attachments for captured OBJECTID {captured_oid}: {e}")
            continue

        for att in attachments:
            att_id = att.get("id")
            att_name = att.get("name", "unknown")
            att_type = att.get("contentType", "application/octet-stream")
            att_size = att.get("size", 0)

            try:
                # Download attachment data
                att_data = captured_layer.attachments.download(att_id)

                # Check if attachment already exists on auth record
                auth_attachments = auth_layer.attachments.get_list(auth_gid)
                existing_names = {a.get("name") for a in auth_attachments}

                if att_name in existing_names:
                    logger.info(
                        f"Attachment \'{att_name}\' already exists on GlobalID {auth_gid}, skipping"
                    )
                    results.append({
                        "captured_objectid": captured_oid,
                        "auth_globalid": auth_gid,
                        "attachment_name": att_name,
                        "status": "skipped",
                    })
                    skipped += 1
                else:
                    # Upload attachment to auth record
                    upload_source = att_data
                    was_temp = False
                    if isinstance(att_data, bytes):
                        # Write to temp file for upload
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{att_name}")
                        tmp.write(att_data)
                        tmp.close()
                        upload_source = tmp.name
                        was_temp = True

                    try:
                        auth_layer.attachments.upload(
                            auth_gid, upload_source, name=att_name, attachment_type=att_type
                        )
                        logger.info(
                            f"Migrated attachment \'{att_name}\' ({att_size} bytes, {att_type}) "
                            f"from OBJECTID {captured_oid} to GlobalID {auth_gid}"
                        )
                        results.append({
                            "captured_objectid": captured_oid,
                            "auth_globalid": auth_gid,
                            "attachment_name": att_name,
                            "status": "migrated",
                        })
                        migrated += 1
                    finally:
                        if was_temp:
                            try:
                                os.unlink(upload_source)
                            except OSError:
                                pass

            except Exception as e:
                logger.error(
                    f"Failed to migrate attachment \'{att_name}\' from OBJECTID {captured_oid} "
                    f"to GlobalID {auth_gid}: {e}"
                )
                results.append({
                    "captured_objectid": captured_oid,
                    "auth_globalid": auth_gid,
                    "attachment_name": att_name,
                    "status": "failed",
                    "error": str(e),
                })
                failed += 1

    print(
        f"Attachment migration complete: {migrated} migrated, {skipped} skipped, {failed} failed"
    )

    return results


def cleanup_checkpoint(checkpoint_path, update_failures, append_failures):
    """Clean up or preserve checkpoint after apply is complete.

    Args:
        checkpoint_path: Path to the checkpoint file.
        update_failures: List of failed update dicts.
        append_failures: List of failed append dicts.
    """
    total_failures = len(update_failures) + len(append_failures)
    if total_failures == 0:
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
        print("Checkpoint deleted — all changes applied successfully")
    else:
        print(f"Checkpoint preserved at {checkpoint_path} — {total_failures} failures remain. Re-run to resume.")
        if update_failures:
            for f in update_failures:
                print(f"  Failed update: GlobalID {f['auth_globalid']}")
        if append_failures:
            for f in append_failures:
                print(f"  Failed append: OBJECTID {f['captured_objectid']}")


def main(argv=None):
    """Main entry point for the conflation tool."""
    # Configure logging for console output
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Parse CLI arguments
    args = parse_args(argv)

    # Load configuration
    config = load_config()

    # Authenticate to AGOL
    gis = authenticate_agol(config)

    # Resolve output paths
    paths = resolve_paths(config, args.layer)

    # Print mode status
    if args.restore:
        print("Mode: RESTORE — Will restore from backup")
    elif args.apply:
        print("Mode: APPLY — Changes will be written to AGOL")
    else:
        print("Mode: DRY RUN — No changes will be written")

    if args.auto_open:
        print("Auto-open review file after dry run: enabled")

    if args.migrate_attachments:
        print("Migrate attachments: enabled")

    print(f"Layer: {args.layer}")

    # Retrieve layer metadata
    captured_info = get_layer_info(gis, config["captured_layer_url"])
    auth_info = get_layer_info(gis, config["auth_layer_url"])

    print(f"Captured layer: {captured_info['layer_name']}")
    print(f"Authoritative layer: {auth_info['layer_name']}")

    # Load and prepare data (Phase 3)
    print("Loading layers...")
    data = prepare_data(
        gis,
        config["captured_layer_url"],
        config["auth_layer_url"],
        captured_info,
        auth_info,
    )
    captured_wgs84 = data["captured_wgs84"]
    auth_wgs84 = data["auth_wgs84"]
    captured_utm = data["captured_utm"]
    auth_utm = data["auth_utm"]

    print(f"Loaded {len(captured_wgs84)} captured features")
    print(f"Loaded {len(auth_wgs84)} authoritative features")

    # Phase 4: Validate schema and create backup
    print("Validating schema...")
    schema_result = validate_schema(auth_wgs84, auth_info)
    print(f"Schema validation passed: notes_max_length={schema_result['notes_max_length']}")

    print("Creating backup...")
    create_backup(auth_wgs84, paths["backup_file"], auth_info["layer_name"])

    print("Phase 4 complete. Ready for matching (Phase 5).")

    # Phase 5: Spatial Indexing & Matching
    print("Building spatial index...")
    auth_sindex = build_spatial_index(auth_utm)

    print("Matching captured points to authoritative points...")
    threshold_ft = config["matching"]["threshold_ft"]
    ambiguity_pct = config["matching"]["ambiguity_pct"]

    # Prepare WGS84 geometry columns for match_points
    captured_utm_with_wgs84 = captured_utm.copy()
    captured_utm_with_wgs84["captured_wgs84_geom"] = captured_wgs84.geometry.values
    auth_utm_with_wgs84 = auth_utm.copy()
    auth_utm_with_wgs84["auth_globalid"] = auth_wgs84.get("GlobalID", None).values if "GlobalID" in auth_wgs84.columns else [None] * len(auth_wgs84)
    auth_utm_with_wgs84["auth_objectid"] = auth_wgs84.get("OBJECTID", None).values if "OBJECTID" in auth_wgs84.columns else [None] * len(auth_wgs84)
    auth_utm_with_wgs84["auth_wgs84_geom"] = auth_wgs84.geometry.values

    match_results = match_points(
        captured_utm_with_wgs84,
        auth_utm_with_wgs84,
        auth_sindex,
        threshold_ft,
        ambiguity_pct,
    )

    # Store in data dict for Phase 6/7
    data["match_results"] = match_results

    # Summary
    clean = sum(1 for r in match_results if r["match_type"] == "clean")
    ambiguous = sum(1 for r in match_results if r["match_type"] == "ambiguous")
    new = sum(1 for r in match_results if r["match_type"] == "new")
    print(f"Matching complete: {clean} clean, {ambiguous} ambiguous, {new} new")

    # Phase 6: Collision Resolution
    print("Detecting collisions...")
    collisions = detect_collisions(match_results)
    if collisions:
        print(f"Resolving {len(collisions)} collision(s)...")
        match_results = resolve_collisions(match_results, collisions)
        # Update summary after collision resolution
        clean = sum(1 for r in match_results if r["match_type"] == "clean")
        ambiguous = sum(1 for r in match_results if r["match_type"] == "ambiguous")
        new = sum(1 for r in match_results if r["match_type"] == "new")
        print(f"After collision resolution: {clean} clean, {ambiguous} ambiguous, {new} new")
    else:
        print("No collisions detected.")

    # Phase 7: Dry Run Output
    print("Writing review file...")
    write_review_geopackage(
        match_results, captured_wgs84, auth_wgs84,
        config["captured_layer_url"], gis,
        paths["review_file"], threshold_ft, auth_info, captured_info,
    )

    print("Writing CSV report...")
    write_report_csv(match_results, paths["report_file"], gis, config["captured_layer_url"])

    # Count total attachments across all matched records
    attachment_count = count_total_attachments(
        match_results, gis, config["captured_layer_url"],
    )
    print_conflation_summary(match_results, attachment_count)

    if args.auto_open:
        auto_open_review(paths["review_file"])

    # Phase 8: Apply Changes
    if args.apply:
        print("\nPhase 8: Applying changes to AGOL...")
        auth_layer = FeatureLayer(url=config["auth_layer_url"], gis=gis)

        checkpoint = manage_checkpoint(paths["checkpoint_file"], args)
        checkpoint["_path"] = paths["checkpoint_file"]
        checkpoint["max_retries"] = config["apply"].get("max_retries", 3)

        batch_size = config["apply"].get("batch_size", 50)

        update_failures = apply_updates_in_batches(
            auth_layer, match_results, checkpoint, schema_result, auth_wgs84, batch_size,
        )

        append_failures = apply_appends_in_batches(
            auth_layer, match_results, checkpoint, schema_result, batch_size,
            use_global_ids=auth_info.get("use_global_ids", False),
        )

        cleanup_checkpoint(paths["checkpoint_file"], update_failures, append_failures)

        # Phase 9: Attachment Migration
        if args.migrate_attachments:
            print("\nPhase 9: Migrating attachments...")
            migration_results = migrate_attachments(
                auth_layer, match_results, config["captured_layer_url"], gis, checkpoint,
            )

            # Update proposed_attachments table with actual migration statuses
            if os.path.exists(paths["review_file"]):
                try:
                    update_review_geopackage_attachments(
                        paths["review_file"], match_results, captured_wgs84, auth_wgs84,
                        config["captured_layer_url"], gis, threshold_ft,
                        auth_info, captured_info, migration_results,
                    )
                except Exception as e:
                    logger.warning(f"Could not update proposed_attachments table: {e}")


if __name__ == "__main__":
    main()
