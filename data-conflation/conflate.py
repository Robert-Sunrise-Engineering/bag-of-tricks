"""Data Conflation Tool - CLI & Initialization.

Merges captured (source) layer data into authoritative (destination) layer,
producing a review file for manual verification before applying changes.
"""

import argparse
import json
import logging
import os
import sys
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


if __name__ == "__main__":
    main()
