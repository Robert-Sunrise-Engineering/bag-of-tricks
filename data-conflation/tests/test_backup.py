"""Tests for conflate.backup module."""

import json

import pytest
from conflate.backup import write_backup, load_backup, load_backup_meta


def test_round_trip_preserves_features_and_types(tmp_path):
    """Test that write_backup and load_backup round-trip features exactly, preserving types."""
    # Create a fixture list with distinct oids and mixed attribute types
    original_features = [
        {
            "oid": 1,
            "attributes": {
                "name": "Feature One",
                "count": 42,
                "ratio": 3.14,
                "active": True,
                "notes": None,
            },
            "geometry": {"x": 10.5, "y": 20.3},
        },
        {
            "oid": 2,
            "attributes": {
                "name": "Feature Two",
                "count": 0,
                "ratio": 2.71828,
                "active": False,
                "notes": "Some text",
            },
            "geometry": {"x": -5.1, "y": 15.9},
        },
        {
            "oid": 3,
            "attributes": {
                "name": "Feature Three",
                "count": 999,
                "ratio": 0.0,
                "active": True,
                "notes": None,
            },
            "geometry": {"x": 0.0, "y": 100.0},
        },
    ]

    # Write to backup file
    backup_path = tmp_path / "test_backup.json"
    write_backup(original_features, backup_path, "hydrants", "https://example.com/FeatureServer/0")

    # Load from backup file
    loaded_features = load_backup(backup_path)

    # Verify exact equality including type preservation
    assert loaded_features == original_features
    assert len(loaded_features) == 3

    # Verify individual fields to ensure type preservation
    for i, (loaded, original) in enumerate(zip(loaded_features, original_features)):
        assert loaded["oid"] == original["oid"]
        assert isinstance(loaded["oid"], int)

        # Check attributes
        assert loaded["attributes"]["count"] == original["attributes"]["count"]
        assert isinstance(loaded["attributes"]["count"], int)

        assert loaded["attributes"]["ratio"] == original["attributes"]["ratio"]
        assert isinstance(loaded["attributes"]["ratio"], float)

        assert loaded["attributes"]["active"] == original["attributes"]["active"]
        assert isinstance(loaded["attributes"]["active"], bool)

        assert loaded["attributes"]["name"] == original["attributes"]["name"]
        assert isinstance(loaded["attributes"]["name"], str)

        assert loaded["attributes"]["notes"] == original["attributes"]["notes"]

        # Check geometry
        assert loaded["geometry"]["x"] == original["geometry"]["x"]
        assert isinstance(loaded["geometry"]["x"], float)

        assert loaded["geometry"]["y"] == original["geometry"]["y"]
        assert isinstance(loaded["geometry"]["y"], float)


def test_load_backup_nonexistent_file_raises_filenotfounderror(tmp_path):
    """Test that load_backup raises FileNotFoundError for a missing file."""
    nonexistent_path = tmp_path / "does_not_exist.json"

    with pytest.raises(FileNotFoundError):
        load_backup(nonexistent_path)


def test_write_backup_records_layer_identity(tmp_path):
    """write_backup stores layer/authoritative_url; load_backup_meta returns them."""
    backup_path = tmp_path / "test_backup.json"
    write_backup([], backup_path, "hydrant_valves", "https://example.com/FeatureServer/9")

    meta = load_backup_meta(backup_path)
    assert meta["layer"] == "hydrant_valves"
    assert meta["authoritative_url"] == "https://example.com/FeatureServer/9"
    assert meta["entries"] == []


def test_load_backup_meta_accepts_legacy_bare_list_format(tmp_path):
    """Backups written before layer identity was tracked are a bare JSON list;
    load_backup_meta should still load them, with layer/authoritative_url as None."""
    legacy_path = tmp_path / "legacy_backup.json"
    legacy_entries = [{"oid": 1, "attributes": {"a": 1}, "geometry": {"x": 0, "y": 0}}]
    with open(legacy_path, "w", encoding="utf-8") as f:
        json.dump(legacy_entries, f)

    meta = load_backup_meta(legacy_path)
    assert meta["layer"] is None
    assert meta["authoritative_url"] is None
    assert meta["entries"] == legacy_entries

    # load_backup (used by rollback.py) still works against legacy files too
    assert load_backup(legacy_path) == legacy_entries
