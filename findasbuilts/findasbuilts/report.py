"""CSV report writing.

verdict_to_row owns the CSV schema (REPORT_COLUMNS); write_report has the
identical contract to data-conflation/conflate/report.py (union of keys in
first-seen order, restval='', empty rows -> empty file, creates parent dir).
ReportWriter is the incremental variant: it writes the header on the first
row and flushes after every row, so an interrupted run still leaves a usable
report behind. read_report reads a report back (for --resume), and
normalize_row restricts a read row to the canonical columns. No console
printing here -- human output lives in cli.py.

All files are opened with ``encoding="utf-8"``: extracted PDF text can
contain characters outside the Windows default (cp1252) — a zero-width
space in a stamp's evidence snippet crashed a live run with
``UnicodeEncodeError`` — so the report must round-trip arbitrary Unicode.
"""

import csv
import json
import os

# The canonical report schema, in column order. verdict_to_row builds rows
# from it, and normalize_row restricts resume-file rows to it, so the header
# of a resumed report is uniform whether or not carried rows are present.
REPORT_COLUMNS = [
    "file_path",
    "verdict",
    "stamp_keywords",
    "stamp_pages",
    "page_count",
    "text_source",
    "evidence",
    "error",
]


def verdict_to_row(verdict) -> dict:
    """
    Convert a Verdict into a CSV report row dict.

    The canonical column order is: ``file_path``, ``verdict``,
    ``stamp_keywords`` (semicolon-joined, config order), ``stamp_pages``
    (JSON dict of designation -> pages), ``page_count``, ``text_source``,
    ``evidence`` (the matched snippet, empty unless ``"STAMP"``), and
    ``error`` (empty unless the verdict is ``"ERROR"``).

    Args:
        verdict: A ``verdict.Verdict``.

    Returns:
        A dict shaped for ``write_report``.
    """
    values = {
        "file_path": verdict.path,
        "verdict": verdict.verdict,
        "stamp_keywords": ";".join(verdict.stamp_keywords),
        "stamp_pages": json.dumps(verdict.stamp_pages),
        "page_count": verdict.page_count,
        "text_source": verdict.text_source,
        "evidence": verdict.evidence,
        "error": verdict.error,
    }
    return {col: values[col] for col in REPORT_COLUMNS}


def normalize_row(row: dict) -> dict:
    """Restrict a row dict to the canonical REPORT_COLUMNS, defaulting
    missing keys to ``""``.

    Resume rows come from ``csv.DictReader`` (all strings) and may carry
    extra columns (a future version's report) or lack columns. Restricting
    them to REPORT_COLUMNS keeps ``csv.DictWriter`` from raising on keys not
    in the header.
    """
    return {col: row.get(col, "") for col in REPORT_COLUMNS}


def read_report(path) -> list[dict]:
    """Read a CSV report into a list of row dicts.

    Args:
        path: The report CSV path.

    Returns:
        A list of row dicts (all values strings, as read from the CSV).
        An empty file yields an empty list.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        ValueError: if the file has a header but no ``file_path`` column.
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames and "file_path" not in reader.fieldnames:
            raise ValueError(f"report is missing the file_path column: {path}")
        return list(reader)


def write_report(rows: list[dict], path) -> None:
    """
    Write rows as a CSV file to path using the csv module.

    Args:
        rows: A list of dicts to write as CSV rows.
        path: The file path where the CSV will be written.

    The CSV header is the UNION of all keys across all dicts in rows.
    Missing keys in a row are filled with empty strings.
    If rows is empty, writes an empty file (no crash).
    Creates parent directory if it doesn't exist.
    """
    # Create parent directory if needed
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    # Handle empty rows list
    if not rows:
        with open(path, 'w', newline='', encoding="utf-8") as f:
            pass
        return

    # Compute the union of all keys across all rows
    # Preserve insertion order by using dict.keys() which maintains order in Python 3.7+
    fieldnames_set = set()
    fieldnames_list = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames_set:
                fieldnames_set.add(key)
                fieldnames_list.append(key)

    # Write CSV with union of fieldnames
    with open(path, 'w', newline='', encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames_list, restval='')
        writer.writeheader()
        writer.writerows(rows)


class ReportWriter:
    """Incrementally write CSV report rows, flushing after every row.

    ``write_report`` writes all rows at once; ``ReportWriter`` writes the
    header on the first ``append`` and flushes to disk after every row, so
    an interrupted run still leaves a usable report behind. The header is
    the first row's keys -- the cli's ``verdict_to_row`` rows are uniform,
    so this matches ``write_report``'s union-of-keys for that caller.

    Callers must ``close()`` the writer (or use it as a context manager) to
    release the file handle; the data itself is on disk after each append.
    """

    def __init__(self, path):
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        self._file = open(path, 'w', newline='', encoding="utf-8")
        self._writer = None

    def append(self, row: dict) -> None:
        """Write one row, writing the header first if this is the first row."""
        if self._writer is None:
            self._writer = csv.DictWriter(
                self._file, fieldnames=list(row.keys()), restval=''
            )
            self._writer.writeheader()
        self._writer.writerow(row)
        self._file.flush()

    def close(self) -> None:
        """Close the underlying file handle."""
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
