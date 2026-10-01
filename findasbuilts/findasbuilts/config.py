import json
import os


def load_config(path) -> dict:
    """
    Reads a JSON file at the given path and returns the parsed dict.

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


def load_config_with_local(path) -> dict:
    """
    Load the config at ``path``, then overlay a sibling ``<name>.local.json``
    if it exists.

    The local file is the machine-specific override layer (e.g. a Tesseract
    binary path): its values win over the base config, merged deep so a
    partial local file (say, only ``ocr.tesseract_cmd``) leaves the rest of
    the base config intact. Lists are replaced wholesale, not concatenated.

    Args:
        path: Path to the base config JSON.

    Returns:
        The merged config dict.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        json.JSONDecodeError: If ``path`` or the local file is not valid JSON.
        ValueError: If the local file is not a JSON object.
    """
    config = load_config(path)
    local_path = _local_config_path(path)
    if os.path.exists(local_path):
        local = load_config(local_path)
        if not isinstance(local, dict):
            raise ValueError(f"Local config must be a JSON object: {local_path}")
        config = _deep_merge(config, local)
    return config


def _local_config_path(path) -> str:
    """Derive the sibling local-config path: ``config.json`` -> ``config.local.json``."""
    root, ext = os.path.splitext(path)
    return root + ".local" + ext


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge ``override`` into ``base``: dicts merge recursively, everything
    else (scalars, lists) is replaced by the override value."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def validate_config(config: dict) -> None:
    """
    Validates the whole-file findasbuilts config dict.

    Rules (each failure raises a ValueError naming the bad key):
      * top-level ``file_extensions`` / ``ocr`` / ``keywords`` present;
      * ``file_extensions`` is a non-empty list of ``.``-prefixed strings;
      * ``ocr.min_chars_for_text_layer`` and ``ocr.ocr_dpi`` are ints > 0;
      * ``ocr.tesseract_lang`` is a non-empty string;
      * ``ocr.tesseract_psm`` is an int or None;
      * ``ocr.tesseract_cmd`` is a string or None;
      * ``keywords.stamp`` is a non-empty list of non-empty strings;
      * ``jobs`` (optional) is an int > 0 when present;
      * ``extraction`` (optional) is an object whose ``open_retries`` is an
        int >= 0 and whose ``retry_delay_seconds`` is a number > 0 when present.

    Args:
        config: The parsed config dict.

    Returns:
        None if the config is valid.

    Raises:
        ValueError: If any rule is violated, naming the bad key.
    """
    for key in ("file_extensions", "ocr", "keywords"):
        if key not in config:
            raise ValueError(f"Missing required config key: {key}")

    # jobs is optional (absent/None -> auto-detect); validated only when
    # present. The isinstance(jobs, bool) guard matters: isinstance(True, int)
    # is True in Python (same guard the min_chars/ocr_dpi rules use).
    jobs = config.get("jobs")
    if jobs is not None and (not isinstance(jobs, int) or isinstance(jobs, bool) or jobs <= 0):
        raise ValueError("jobs must be an int > 0")

    # extraction is optional (absent -> extract.py's module defaults);
    # validated only when present.
    extraction = config.get("extraction")
    if extraction is not None:
        if not isinstance(extraction, dict):
            raise ValueError("extraction must be an object")
        open_retries = extraction.get("open_retries")
        if open_retries is not None and (
            not isinstance(open_retries, int)
            or isinstance(open_retries, bool)
            or open_retries < 0
        ):
            raise ValueError("extraction.open_retries must be an int >= 0")
        retry_delay = extraction.get("retry_delay_seconds")
        if retry_delay is not None and (
            not isinstance(retry_delay, (int, float))
            or isinstance(retry_delay, bool)
            or retry_delay <= 0
        ):
            raise ValueError("extraction.retry_delay_seconds must be a number > 0")

    extensions = config["file_extensions"]
    if not isinstance(extensions, list) or not extensions:
        raise ValueError("file_extensions must be a non-empty list")
    for ext in extensions:
        if not isinstance(ext, str) or not ext.startswith("."):
            raise ValueError(f"file_extensions entries must be '.'-prefixed strings, got {ext!r}")

    ocr = config["ocr"]
    if not isinstance(ocr, dict):
        raise ValueError("ocr must be an object")

    min_chars = ocr.get("min_chars_for_text_layer")
    if not isinstance(min_chars, int) or isinstance(min_chars, bool) or min_chars <= 0:
        raise ValueError("ocr.min_chars_for_text_layer must be an int > 0")

    ocr_dpi = ocr.get("ocr_dpi")
    if not isinstance(ocr_dpi, int) or isinstance(ocr_dpi, bool) or ocr_dpi <= 0:
        raise ValueError("ocr.ocr_dpi must be an int > 0")

    tesseract_lang = ocr.get("tesseract_lang")
    if not isinstance(tesseract_lang, str) or not tesseract_lang.strip():
        raise ValueError("ocr.tesseract_lang must be a non-empty string")

    tesseract_psm = ocr.get("tesseract_psm")
    if tesseract_psm is not None and (not isinstance(tesseract_psm, int) or isinstance(tesseract_psm, bool)):
        raise ValueError("ocr.tesseract_psm must be an int or None")

    tesseract_cmd = ocr.get("tesseract_cmd")
    if tesseract_cmd is not None and not isinstance(tesseract_cmd, str):
        raise ValueError("ocr.tesseract_cmd must be a string or None")

    keywords = config["keywords"]
    if not isinstance(keywords, dict):
        raise ValueError("keywords must be an object")

    stamp = keywords.get("stamp")
    if not isinstance(stamp, list) or not stamp:
        raise ValueError("keywords.stamp must be a non-empty list")
    for kw in stamp:
        if not isinstance(kw, str) or not kw.strip():
            raise ValueError(f"keywords.stamp entries must be non-empty strings, got {kw!r}")
