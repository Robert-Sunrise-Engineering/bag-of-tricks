"""Shared field-name constants for conflation writes.

Kept in their own module (rather than defined once in ``cli.py``) so both
``cli.py`` (building update/append payloads) and ``rollback.py`` (building
restore payloads) enforce the same exclusion list from a single source of
truth.
"""

# Fields that must never be written by null-fill, append, or restore payloads:
# system/editor-tracking fields that AGOL manages itself and that a client
# should never attempt to set directly.
EXCLUDED_FIELDS = {
    "OBJECTID",
    "GlobalID",
    "Shape",
    "SHAPE",
    "Creator",
    "CreationDate",
    "Editor",
    "EditDate",
}
