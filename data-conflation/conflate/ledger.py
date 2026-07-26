"""Ledger module for tracking processed features in AGOL conflation."""

import json
import os


def load_ledger(path: str) -> dict:
    """
    Load a ledger from a JSON file.

    If the file does not exist, returns an empty dict without raising.

    Args:
        path: Path to the ledger JSON file.

    Returns:
        Dictionary mapping captured GlobalID (string) to record dict, or {} if file doesn't exist.
    """
    if not os.path.exists(path):
        return {}

    with open(path, 'r') as f:
        return json.load(f)


def save_ledger(path: str, ledger: dict) -> None:
    """
    Save a ledger to a JSON file.

    Creates parent directories if needed.

    Args:
        path: Path where the ledger JSON file should be written.
        ledger: Dictionary to save.
    """
    dir_path = os.path.dirname(path)
    if dir_path:  # Only call makedirs if path has a directory component
        os.makedirs(dir_path, exist_ok=True)

    with open(path, 'w') as f:
        json.dump(ledger, f, indent=2)


def mark_processed(
    ledger: dict,
    captured_global_id: str,
    action: str,
    authoritative_oid,
    attachments_status: str,
    run_time: str,
) -> None:
    """
    Mark a feature as processed in the ledger.

    Mutates the ledger dict in place.

    Args:
        ledger: The ledger dict to mutate.
        captured_global_id: The GlobalID of the captured feature.
        action: Action performed (e.g., "updated", "created", "skipped").
        authoritative_oid: OID from the authoritative source.
        attachments_status: String describing attachment status (e.g., "2/3").
        run_time: ISO format timestamp of when the action was performed.
    """
    ledger[captured_global_id] = {
        "action": action,
        "authoritative_oid": authoritative_oid,
        "attachments_status": attachments_status,
        "run_time": run_time,
    }


def is_processed(ledger: dict, captured_global_id: str) -> bool:
    """
    Check if a feature has been processed.

    Args:
        ledger: The ledger dict.
        captured_global_id: The GlobalID to check.

    Returns:
        True if the GlobalID exists in the ledger, False otherwise.
    """
    return captured_global_id in ledger
