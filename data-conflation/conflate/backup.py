"""Backup snapshot format for pre-write feature state.

A backup snapshot is a pre-write copy of every authoritative-layer feature
that a run is about to touch (via update), captured BEFORE any write
happens, so a later rollback can restore prior state.
"""

import json
import os


def write_backup(features: list[dict], path) -> None:
    """Serialize ``features`` as a JSON list to ``path``.

    Each entry in ``features`` is expected to be shaped
    ``{"oid": <int>, "attributes": {...}, "geometry": {...}}`` and represent
    the PRE-EDIT state of an authoritative feature about to be updated.

    Creates the parent directory of ``path`` if it doesn't exist and isn't
    an empty string.
    """
    parent = os.path.dirname(str(path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(features, f)


def load_backup(path) -> list[dict]:
    """Read and parse the JSON backup file at ``path``.

    Returns the list of feature dicts in the exact shape written by
    ``write_backup``. Propagates ``FileNotFoundError`` and
    ``json.JSONDecodeError`` naturally if the path is missing or invalid.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
