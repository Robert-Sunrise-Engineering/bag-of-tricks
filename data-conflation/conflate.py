"""Data Conflation Tool - CLI & Initialization.

Merges captured (source) layer data into authoritative (destination) layer,
producing a review file for manual verification before applying changes.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from arcgis.features import FeatureLayer
from arcgis.gis import GIS


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
# Main Entry Point
# ---------------------------------------------------------------------------

def main(argv=None):
    """Main entry point for the conflation tool."""
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


if __name__ == "__main__":
    main()
