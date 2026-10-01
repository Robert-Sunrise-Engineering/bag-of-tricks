"""Tests for findasbuilts.report.

Covers verdict_to_row's CSV schema, write_report's union-of-keys /
empty-rows / parent-dir contract (identical to the sibling
data-conflation/conflate/report.py), ReportWriter's incremental
header-on-first-row / flush-per-row behavior, and read_report /
normalize_row (the --resume read path).
"""

import csv
import json

import pytest

from findasbuilts.report import (
    REPORT_COLUMNS,
    ReportWriter,
    normalize_row,
    read_report,
    verdict_to_row,
    write_report,
)
from findasbuilts.verdict import Verdict


def _verdict(**over):
    v = {
        "path": "C:/docs/a.pdf",
        "has_stamp": True,
        "verdict": "STAMP",
        "stamp_keywords": ["p.e.", "license no"],
        "stamp_pages": {"p.e.": [2], "license no": [2]},
        "page_count": 3,
        "text_source": "text_layer",
        "evidence": "P.E. No. 12345",
        "error": "",
    }
    v.update(over)
    return Verdict(**v)


class TestVerdictToRow:
    def test_column_set_and_order(self):
        row = verdict_to_row(_verdict())
        assert list(row.keys()) == REPORT_COLUMNS

    def test_stamp_keywords_semicolon_joined_in_config_order(self):
        row = verdict_to_row(_verdict())
        assert row["stamp_keywords"] == "p.e.;license no"

    def test_stamp_pages_json_encoded(self):
        row = verdict_to_row(_verdict())
        assert json.loads(row["stamp_pages"]) == {"p.e.": [2], "license no": [2]}

    def test_evidence_emitted(self):
        row = verdict_to_row(_verdict())
        assert row["evidence"] == "P.E. No. 12345"

    def test_no_stamp_row(self):
        row = verdict_to_row(
            _verdict(
                has_stamp=False,
                verdict="NO_STAMP",
                stamp_keywords=[],
                stamp_pages={},
                evidence="",
            )
        )
        assert row["stamp_keywords"] == ""
        assert json.loads(row["stamp_pages"]) == {}
        assert row["evidence"] == ""

    def test_error_row_carries_error(self):
        row = verdict_to_row(
            _verdict(verdict="ERROR", has_stamp=False, error="boom")
        )
        assert row["verdict"] == "ERROR"
        assert row["error"] == "boom"


class TestWriteReport:
    def test_writes_union_of_keys(self, tmp_path):
        path = tmp_path / "reports" / "out.csv"
        rows = [
            {"file_path": "a.pdf", "verdict": "STAMP", "extra": "x"},
            {"file_path": "b.pdf", "verdict": "NO_STAMP"},
        ]
        write_report(rows, str(path))
        with open(path, newline="", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
        assert reader[0]["file_path"] == "a.pdf"
        assert reader[0]["extra"] == "x"
        # Missing key filled with empty string.
        assert reader[1]["extra"] == ""

    def test_empty_rows_writes_empty_file(self, tmp_path):
        path = tmp_path / "reports" / "empty.csv"
        write_report([], str(path))
        assert path.read_text(encoding="utf-8") == ""

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "a" / "b" / "c" / "out.csv"
        write_report([{"file_path": "a.pdf", "verdict": "STAMP"}], str(path))
        assert path.exists()

    def test_roundtrip_verdict_rows(self, tmp_path):
        path = tmp_path / "reports" / "out.csv"
        rows = [verdict_to_row(_verdict()), verdict_to_row(_verdict(verdict="NO_STAMP", has_stamp=False, stamp_keywords=[], stamp_pages={}))]
        write_report(rows, str(path))
        with open(path, newline="", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
        assert len(reader) == 2
        assert reader[0]["verdict"] == "STAMP"
        assert reader[0]["stamp_keywords"] == "p.e.;license no"
        assert reader[1]["verdict"] == "NO_STAMP"

    def test_unicode_evidence_roundtrips(self, tmp_path):
        # Extracted PDF text can carry characters outside cp1252 (a
        # zero-width space ​ crashed a live run); the report must
        # round-trip arbitrary Unicode.
        path = tmp_path / "reports" / "unicode.csv"
        row = verdict_to_row(_verdict(evidence="P.E.​ No. 12345"))
        write_report([row], str(path))
        with open(path, newline="", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
        assert reader[0]["evidence"] == "P.E.​ No. 12345"


class TestReadReport:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "reports" / "out.csv"
        rows = [
            verdict_to_row(_verdict()),
            verdict_to_row(_verdict(verdict="NO_STAMP", has_stamp=False, stamp_keywords=[], stamp_pages={})),
        ]
        write_report(rows, str(path))
        read = read_report(str(path))
        assert len(read) == 2
        # Values come back as strings, matching what was written.
        assert read[0]["file_path"] == "C:/docs/a.pdf"
        assert read[0]["verdict"] == "STAMP"
        assert read[0]["stamp_keywords"] == "p.e.;license no"
        assert json.loads(read[0]["stamp_pages"]) == {"p.e.": [2], "license no": [2]}
        assert read[0]["page_count"] == "3"
        assert read[0]["text_source"] == "text_layer"
        assert read[0]["evidence"] == "P.E. No. 12345"
        assert read[0]["error"] == ""
        assert read[1]["verdict"] == "NO_STAMP"

    def test_empty_file_returns_empty_list(self, tmp_path):
        path = tmp_path / "reports" / "empty.csv"
        write_report([], str(path))
        assert read_report(str(path)) == []

    def test_missing_file_path_column_raises_value_error(self, tmp_path):
        path = tmp_path / "reports" / "bad.csv"
        write_report([{"verdict": "STAMP"}], str(path))
        with pytest.raises(ValueError):
            read_report(str(path))

    def test_nonexistent_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_report(str(tmp_path / "does-not-exist.csv"))


class TestNormalizeRow:
    def test_restricts_to_canonical_columns(self):
        row = normalize_row({"file_path": "a.pdf", "verdict": "STAMP", "extra": "x"})
        assert list(row.keys()) == REPORT_COLUMNS
        assert "extra" not in row

    def test_missing_columns_default_to_empty(self):
        row = normalize_row({"file_path": "a.pdf"})
        assert row["verdict"] == ""
        assert row["error"] == ""
        assert row["stamp_keywords"] == ""
        # Old resume files predate the evidence column -- it defaults to "".
        assert row["evidence"] == ""


class TestReportWriter:
    def test_writes_header_and_rows(self, tmp_path):
        path = tmp_path / "reports" / "out.csv"
        writer = ReportWriter(str(path))
        writer.append({"file_path": "a.pdf", "verdict": "STAMP", "error": ""})
        writer.append({"file_path": "b.pdf", "verdict": "ERROR", "error": "boom"})
        writer.close()
        with open(path, newline="", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
        assert len(reader) == 2
        assert reader[0]["file_path"] == "a.pdf"
        assert reader[0]["verdict"] == "STAMP"
        assert reader[0]["error"] == ""
        assert reader[1]["verdict"] == "ERROR"
        assert reader[1]["error"] == "boom"

    def test_flushes_after_each_append(self, tmp_path):
        path = tmp_path / "reports" / "out.csv"
        writer = ReportWriter(str(path))
        writer.append({"file_path": "a.pdf", "verdict": "STAMP"})
        # The row is readable before close() -- an interrupted run keeps it.
        with open(path, newline="", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
        assert len(reader) == 1
        assert reader[0]["file_path"] == "a.pdf"
        writer.close()

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "a" / "b" / "c" / "out.csv"
        writer = ReportWriter(str(path))
        writer.append({"file_path": "a.pdf", "verdict": "STAMP"})
        writer.close()
        assert path.exists()

    def test_context_manager_closes(self, tmp_path):
        path = tmp_path / "reports" / "out.csv"
        with ReportWriter(str(path)) as writer:
            writer.append({"file_path": "a.pdf", "verdict": "STAMP"})
        # File is closed and readable after the with-block.
        with open(path, newline="", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
        assert len(reader) == 1
