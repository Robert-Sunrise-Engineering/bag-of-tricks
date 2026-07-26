import json


def load_config(path) -> dict:
    """
    Reads a JSON file at the given path and returns the parsed dict.
    Expected to have a top-level "layers" key mapping layer names to layer-config dicts.

    Args:
        path: File path to the JSON configuration file.

    Returns:
        Parsed dictionary from the JSON file.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    with open(path) as f:
        return json.load(f)


def load_local_config(path) -> dict:
    """
    Reads a JSON file at the given path containing local configuration.
    Expected to have "portal_url", "username", and "password" keys.

    Args:
        path: File path to the JSON configuration file.

    Returns:
        Parsed dictionary from the JSON file.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    with open(path) as f:
        return json.load(f)


def validate_layer_config(layer_cfg: dict) -> None:
    """
    Validates that a layer configuration dict contains all required keys.

    "type_field_authoritative"/"type_field_captured" are optional — most layers
    have no type attribute to match on and rely on spatial proximity alone.
    They must be specified together (both present or both absent); specifying
    only one is almost certainly a typo of the other's field name.

    Args:
        layer_cfg: Dictionary containing layer configuration.

    Returns:
        None if all required keys are present.

    Raises:
        ValueError: If any required key is missing, with a message naming the missing key.
    """
    required_keys = {
        "authoritative_url",
        "captured_url",
        "match_threshold_m",
        "field_map",
        "copy_attachments",
    }

    for key in required_keys:
        if key not in layer_cfg:
            raise ValueError(f"Missing required config key: {key}")

    has_type_authoritative = "type_field_authoritative" in layer_cfg
    has_type_captured = "type_field_captured" in layer_cfg
    if has_type_authoritative != has_type_captured:
        raise ValueError(
            "type_field_authoritative and type_field_captured must be specified "
            "together, or not at all"
        )
