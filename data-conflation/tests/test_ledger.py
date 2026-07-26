"""Tests for the ledger module."""

import pytest
from conflate.ledger import (
    load_ledger,
    save_ledger,
    mark_processed,
    is_processed,
)


def test_load_ledger_nonexistent_path_returns_empty_dict(tmp_path):
    """Test that load_ledger returns {} when the path doesn't exist."""
    nonexistent_file = tmp_path / "does_not_exist.json"
    result = load_ledger(str(nonexistent_file))
    assert result == {}


def test_mark_processed_and_roundtrip(tmp_path):
    """Test that mark_processed data correctly round-trips through save/load."""
    # Setup test data
    ledger_file = tmp_path / "ledger.json"
    captured_global_id = "test-global-id-123"
    action = "updated"
    authoritative_oid = 42
    attachments_status = "2/3"
    run_time = "2026-07-25T14:30:00Z"

    # Create empty ledger and mark it as processed
    ledger = {}
    mark_processed(
        ledger,
        captured_global_id,
        action,
        authoritative_oid,
        attachments_status,
        run_time,
    )

    # Save the ledger
    save_ledger(str(ledger_file), ledger)

    # Load it back
    loaded_ledger = load_ledger(str(ledger_file))

    # Verify all 5 pieces of information are present and correct
    assert captured_global_id in loaded_ledger
    record = loaded_ledger[captured_global_id]
    assert record["action"] == action
    assert record["authoritative_oid"] == authoritative_oid
    assert record["attachments_status"] == attachments_status
    assert record["run_time"] == run_time


def test_is_processed_with_same_ledger_dict(tmp_path):
    """Test that is_processed correctly returns True/False for marked/unmarked entries."""
    ledger = {}
    marked_id = "marked-id-001"
    unmarked_id = "unmarked-id-002"

    # Mark one ID as processed
    mark_processed(
        ledger,
        marked_id,
        action="updated",
        authoritative_oid=100,
        attachments_status="1/1",
        run_time="2026-07-25T10:00:00Z",
    )

    # Test is_processed
    assert is_processed(ledger, marked_id) is True
    assert is_processed(ledger, unmarked_id) is False
