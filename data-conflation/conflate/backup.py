"""Backup snapshot format for pre-write feature state.

A backup snapshot is a pre-write copy of every authoritative-layer feature
that a run is about to touch (via update), captured BEFORE any write
happens, so a later rollback can restore prior state.

Every backup also records which layer it was taken from (``layer`` config
key and ``authoritative_url``), so a rollback can refuse to run against the
wrong layer instead of silently deleting/restoring the wrong service (see
``conflate.rollback``).
"""

import json
import os


def write_backup(features: list[dict], path, layer: str, authoritative_url: str) -> None:
    """Serialize ``features`` as a JSON object to ``path``.

    Each entry in ``features`` is expected to be shaped
    ``{"oid": <int>, "attributes": {...}, "geometry": {...}}`` and represent
    the PRE-EDIT state of an authoritative feature about to be updated.

    ``layer`` (the config.json layer key) and ``authoritative_url`` are
    stored alongside the entries so a later rollback can verify it's being
    run against the same layer this backup was taken from.

    Creates the parent directory of ``path`` if it doesn't exist and isn't
    an empty string.
    """
    parent = os.path.dirname(str(path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    payload = {"layer": layer, "authoritative_url": authoritative_url, "entries": features}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def load_backup_meta(path) -> dict:
    """Read and parse the JSON backup file at ``path``.

    Returns ``{"layer": ..., "authoritative_url": ..., "entries": [...]}``.
    Backups written before layer identity was tracked are a bare JSON list;
    those are returned as ``{"layer": None, "authoritative_url": None,
    "entries": <the list>}`` so older backups remain loadable.

    Propagates ``FileNotFoundError`` and ``json.JSONDecodeError`` naturally
    if the path is missing or invalid.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return {"layer": None, "authoritative_url": None, "entries": data}
    return data


def load_backup(path) -> list[dict]:
    """Read and parse the JSON backup file at ``path``, returning just the
    feature entries (see ``load_backup_meta`` for the full payload including
    layer identity).
    """
    return load_backup_meta(path)["entries"]
