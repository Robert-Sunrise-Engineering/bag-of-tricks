"""Interactive setup script to create config.local.json with AGOL credentials and layer URLs."""

import json
import os
import sys
from getpass import getpass
from arcgis.gis import GIS


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_LOCAL_PATH = os.path.join(SCRIPT_DIR, "config.local.json")


def prompt_credentials():
    """Prompt user for AGOL username and password."""
    print("Step 1: AGOL Credentials")
    print("-" * 40)
    username = input("AGOL username: ")
    password = getpass("AGOL password: ")
    print()
    return username, password


def prompt_url(label):
    """Prompt user for a URL, retrying on empty input."""
    while True:
        url = input(f"{label}: ").strip()
        if url:
            return url
        print("URL cannot be empty. Please try again.")


def validate_url(gis, url, label):
    """Validate that a URL points to an accessible Feature Layer. Returns the layer or None."""
    while True:
        print(f"\nValidating {label}...")
        try:
            layer = gis.content.get(url)
            if layer is None:
                print(f"URL does not point to a valid Feature Layer: {url}")
                retry = input("Retry? [y/N]: ").strip().lower()
                if retry == "y":
                    continue
                return None
            if layer.type != "Feature Layer":
                print(f"URL does not point to a valid Feature Layer: {url}")
                print(f"Found type: '{layer.type}'. Expected 'Feature Layer'.")
                retry = input("Retry? [y/N]: ").strip().lower()
                if retry == "y":
                    continue
                return None
            print(f"  Layer name: {layer.title}")
            print(f"  Layer type: {layer.type}")
            return layer
        except Exception as e:
            print(f"URL does not point to a valid Feature Layer: {url}")
            print(f"Error: {e}")
            retry = input("Retry? [y/N]: ").strip().lower()
            if retry == "y":
                continue
            return None


def get_feature_count(layer):
    """Get the feature count from a layer."""
    try:
        return layer.item.itemInfo.attributes.get("Size", None) or layer.item.itemInfo.attributes.get("numFeatures", None)
    except Exception:
        pass

    try:
        features = layer.query(return_count_only=True)
        return features
    except Exception:
        return "unknown"


def get_fields(layer):
    """Get field names from a layer."""
    try:
        fields = layer.fields
        return [f.get("name", "") for f in fields if f.get("name")]
    except Exception:
        return []


def display_layer_info(captured_layer, auth_layer):
    """Display layer information for user review."""
    print("\n" + "=" * 40)
    print("Layer Information")
    print("=" * 40)

    print(f"\nCaptured Layer:")
    print(f"  Name: {captured_layer.title}")
    count = get_feature_count(captured_layer)
    print(f"  Feature count: {count}")
    fields = get_fields(captured_layer)
    print(f"  Fields: {', '.join(fields)}")

    print(f"\nAuthoritative Layer:")
    print(f"  Name: {auth_layer.title}")
    count = get_feature_count(auth_layer)
    print(f"  Feature count: {count}")
    fields = get_fields(auth_layer)
    print(f"  Fields: {', '.join(fields)}")


def write_config(username, password, captured_url, auth_url):
    """Write config.local.json."""
    config = {
        "agol": {
            "username": username,
            "password": password
        },
        "captured_layer_url": captured_url,
        "auth_layer_url": auth_url
    }
    with open(CONFIG_LOCAL_PATH, "w") as f:
        json.dump(config, f, indent=2)
    print(f"\nConfiguration written to {CONFIG_LOCAL_PATH}")


def main():
    """Main setup flow."""
    print("=" * 40)
    print("Data Conflation Configuration Setup")
    print("=" * 40)
    print()

    # Check if config.local.json already exists
    if os.path.exists(CONFIG_LOCAL_PATH):
        response = input("config.local.json already exists. Overwrite? [y/N]: ").strip().lower()
        if response != "y":
            print("Aborted. No changes made.")
            sys.exit(0)

    # Step 1: Credentials
    username, password = prompt_credentials()

    # Authenticate to AGOL
    print("Step 2: Authenticating to AGOL...")
    print("-" * 40)
    try:
        gis = None
        # Try AGOL first, then allow custom portal
        print("  Connecting to arcgis.com (AGOL)...")
        gis = GIS(url="https://www.arcgis.com/sharing/rest", username=username, password=password)
        print(f"  Authenticated as: {gis.properties.username}")
    except Exception as e:
        print(f"Could not authenticate to AGOL. Please check your credentials.")
        print(f"Error: {e}")
        sys.exit(1)

    # Step 3: Layer URLs
    print("\nStep 3: Layer URLs")
    print("-" * 40)
    captured_url = prompt_url("Captured layer URL (FeatureServer/0 endpoint)")
    auth_url = prompt_url("Authoritative layer URL (FeatureServer/0 endpoint)")

    # Step 4: Validate URLs
    print("\nStep 4: Validating Layers")
    print("-" * 40)

    captured_layer = validate_url(gis, captured_url, "Captured Layer")
    auth_layer = validate_url(gis, auth_url, "Authoritative Layer")

    if captured_layer is None or auth_layer is None:
        print("\nOne or both URLs are invalid. Aborting setup.")
        sys.exit(1)

    # Step 5: Display layer info
    display_layer_info(captured_layer, auth_layer)

    # Step 6: Write config
    print("\n" + "=" * 40)
    print("Writing Configuration")
    print("=" * 40)
    write_config(username, password, captured_url, auth_url)
    print("\nSetup complete!")


if __name__ == "__main__":
    main()
