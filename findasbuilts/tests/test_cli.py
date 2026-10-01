"""Tests for findasbuilts.cli helpers.

Covers _build_arg_parser's defaults and the argparse-level usage errors
(missing DIRECTORY, --min-chars < 1) that exit 2, plus _row_to_verdict
(the resume-row reconstruction). main()'s full pipeline is exercised in
test_cli_main.py.
"""

import pytest

from findasbuilts.cli import _build_arg_parser, _row_to_verdict


class TestBuildArgParser:
    def test_defaults(self):
        parser = _build_arg_parser()
        args = parser.parse_args(["some/dir"])
        assert args.directory == "some/dir"
        assert args.config == "config.json"
        assert args.report_dir == "reports"
        assert args.min_chars is None
        assert args.jobs is None
        assert args.resume is None
        assert args.verbose is False

    def test_prog_is_findasbuilts(self):
        parser = _build_arg_parser()
        assert parser.prog == "findasbuilts"

    def test_overrides(self):
        parser = _build_arg_parser()
        args = parser.parse_args(
            ["some/dir", "--config", "config.local.json", "--report-dir", "out",
             "--min-chars", "50", "--jobs", "4", "--verbose"]
        )
        assert args.config == "config.local.json"
        assert args.report_dir == "out"
        assert args.min_chars == 50
        assert args.jobs == 4
        assert args.verbose is True

    def test_missing_directory_is_usage_error(self):
        parser = _build_arg_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args([])
        assert exc.value.code == 2


class TestRowToVerdict:
    def test_reconstructs_full_verdict(self):
        row = {
            "file_path": "C:/docs/a.pdf",
            "verdict": "STAMP",
            "stamp_keywords": "p.e.;license no",
            "stamp_pages": '{"p.e.": [2]}',
            "page_count": "3",
            "text_source": "text_layer",
            "evidence": "P.E. No. 12345",
            "error": "",
        }
        v = _row_to_verdict(row)
        assert v.path == "C:/docs/a.pdf"
        assert v.has_stamp is True
        assert v.verdict == "STAMP"
        assert v.stamp_keywords == ["p.e.", "license no"]
        assert v.stamp_pages == {"p.e.": [2]}
        assert v.page_count == 3
        assert v.text_source == "text_layer"
        assert v.evidence == "P.E. No. 12345"
        assert v.error == ""

    def test_empty_cells_are_guarded(self):
        row = {
            "file_path": "C:/docs/b.pdf",
            "verdict": "NO_STAMP",
            "stamp_keywords": "",
            "stamp_pages": "",
            "page_count": "",
            "text_source": "",
            "evidence": "",
            "error": "",
        }
        v = _row_to_verdict(row)
        assert v.has_stamp is False
        assert v.stamp_keywords == []
        assert v.stamp_pages == {}
        assert v.page_count == 0
        assert v.evidence == ""
