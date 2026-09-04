import json
import math
import os
import tempfile


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

    # A non-positive or non-finite match_threshold_m collapses assign_matches's
    # cost matrix (k=0 -> every captured feature matches nothing, or k=inf ->
    # every captured feature matches every authoritative feature), silently
    # accepting arbitrary matches. The cli guards --default-threshold-m and
    # calibration output against this; validate it here too so a hand-edited
    # config can't sneak a bad value (0, negative, NaN, inf) past the normal run.
    threshold = layer_cfg["match_threshold_m"]
    if (
        not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or threshold <= 0
    ):
        raise ValueError(
            f"match_threshold_m must be a positive finite number, got {threshold!r}"
        )

    has_type_authoritative = "type_field_authoritative" in layer_cfg
    has_type_captured = "type_field_captured" in layer_cfg
    if has_type_authoritative != has_type_captured:
        raise ValueError(
            "type_field_authoritative and type_field_captured must be specified "
            "together, or not at all"
        )


def save_config(path, config: dict) -> None:
    """
    Write a config dict to JSON at ``path``, atomically.

    Pretty-prints with 2-space indent (matching config.json's current style)
    and writes via a temp file in the same directory plus ``os.replace``, so a
    crash mid-write can't leave config.json half-truncated. There is no dry-run
    gate on auto-configure, so the atomic write is the crash-safety net.

    Byte-style is written deterministically regardless of platform: CRLF line
    endings and no trailing newline, byte-identical to the existing config.json
    (CRLF, ends at ``}``). This matters because auto-configure's "unchanged"
    bucket is only meaningful if a no-op re-run produces a clean ``git diff`` --
    a stray LF->CRLF flip or an appended trailing newline would diff every
    untouched line.

    Args:
        path: File path to write.
        config: Dictionary to serialize.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as f:
            # json.dump emits "\n" separators; newline="\r\n" translates each to
            # CRLF. json.dump does not append a trailing newline, matching the
            # existing file's no-trailing-newline style.
            json.dump(config, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
