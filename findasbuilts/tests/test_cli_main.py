"""Integration-level tests for findasbuilts.cli.main().

tests/test_cli.py covers cli.py's pure helpers (_build_arg_parser) in
isolation. These tests drive main() itself with the cli module's own
imports monkeypatched (cli.extract_text, cli.detect_stamp,
cli.evaluate_document, cli.datetime with a fake _Clock), so the full
wiring -- scan -> extract -> detect -> evaluate -> report -> console -- is
exercised with no real PDFs, no Tesseract binary, and no network.
"""

import csv
import glob
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

import findasbuilts.cli as cli
from findasbuilts.extract import ExtractionResult, aggregate_source
from findasbuilts.keywords import KeywordMatch
from findasbuilts.report import verdict_to_row, write_report
from findasbuilts.stamp import detect_stamp
from findasbuilts.verdict import Verdict, evaluate_document
from pytesseract import TesseractNotFoundError


def _valid_config(**over):
    cfg = {
        "file_extensions": [".pdf", ".tif"],
        "ocr": {
            "min_chars_for_text_layer": 20,
            "ocr_dpi": 300,
            "tesseract_lang": "eng",
            "tesseract_psm": None,
            "tesseract_cmd": None,
        },
        "keywords": {"stamp": ["p.e.", "professional engineer"]},
    }
    cfg.update(over)
    return cfg


class _Clock:
    """Patched in for cli.datetime so tests control the report timestamp
    instead of racing real wall-clock seconds."""

    def __init__(self, start):
        self.current = start

    def now(self):
        return self.current


def _default_stamp(text_by_page, keywords):
    return detect_stamp(text_by_page, keywords)


def _default_source(source_by_page):
    return aggregate_source(source_by_page)


def _default_evaluate(path, stamp, *, page_count, text_source):
    return evaluate_document(path, stamp, page_count=page_count, text_source=text_source)


def _run_main(
    monkeypatch,
    tmp_path,
    *,
    documents,
    extract,
    clock,
    stamp=None,
    source=None,
    evaluate=None,
    argv_extra=None,
    config=None,
):
    """Drive cli.main() with fakes for every I/O touch point. ``extract`` is
    required (the I/O boundary); stamp/source/evaluate default to the real
    pure functions so the wiring is still exercised end-to-end. ``config``
    overrides the default valid config (e.g. to set a ``jobs`` value)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "some/dir").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        cli, "load_config_with_local",
        lambda path: config if config is not None else _valid_config(),
    )
    monkeypatch.setattr(cli, "validate_config", lambda config: None)
    monkeypatch.setattr(cli, "iter_documents", lambda root, extensions: documents)
    # The extract fakes take (path, ocr_settings); wrap them so the
    # extraction_settings arg cli now passes (retry knobs) is absorbed.
    monkeypatch.setattr(
        cli,
        "extract_text",
        lambda path, ocr_settings, extraction_settings=None: extract(path, ocr_settings),
    )
    monkeypatch.setattr(cli, "detect_stamp", stamp or _default_stamp)
    monkeypatch.setattr(cli, "aggregate_source", source or _default_source)
    monkeypatch.setattr(cli, "evaluate_document", evaluate or _default_evaluate)
    monkeypatch.setattr(cli, "datetime", clock)

    argv = ["findasbuilts", "some/dir"]
    if argv_extra:
        argv.extend(argv_extra)
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()


def _latest_report_rows(tmp_path):
    paths = sorted(glob.glob(str(tmp_path / "reports" / "findasbuilts_*.csv")))
    with open(paths[-1], newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _text_layer_result(text):
    return ExtractionResult({1: text}, {1: "text_layer"}, 1)


class TestHappyPath:
    def test_writes_csv_and_console(self, monkeypatch, tmp_path, capsys):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        doc_b = tmp_path / "some/dir" / "b.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))

        def extract(path, ocr_settings):
            if path == str(doc_a):
                return _text_layer_result("P.E. No. 123456")
            return _text_layer_result("plain prose about a water system")

        _run_main(
            monkeypatch, tmp_path,
            documents=[doc_a, doc_b], extract=extract, clock=clock,
        )

        rows = _latest_report_rows(tmp_path)
        assert len(rows) == 2
        # Completion order is non-deterministic under the thread pool, so
        # assert by file_path, not by row position.
        by_path = {r["file_path"]: r for r in rows}
        assert by_path[str(doc_a)]["verdict"] == "STAMP"
        assert by_path[str(doc_a)]["stamp_keywords"] == "p.e."
        assert by_path[str(doc_a)]["page_count"] == "1"
        assert by_path[str(doc_a)]["text_source"] == "text_layer"
        assert by_path[str(doc_a)]["evidence"] == "P.E. No. 123456"
        assert by_path[str(doc_a)]["error"] == ""
        assert by_path[str(doc_b)]["verdict"] == "NO_STAMP"
        assert by_path[str(doc_b)]["evidence"] == ""

        out = capsys.readouterr().out
        assert "Scanned 2 document(s) in some/dir" in out
        assert "Stamped (1):" in out
        assert str(doc_a) in out
        assert "Not stamped (1):" in out
        assert str(doc_b) in out
        assert f"Report: {os.path.join('reports', 'findasbuilts_20260904_100000.csv')}" in out

    def test_report_filename_uses_fake_clock(self, monkeypatch, tmp_path):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        _run_main(
            monkeypatch, tmp_path,
            documents=[doc_a], extract=lambda path, s: _text_layer_result("LICENSE NO 12345"),
            clock=clock,
        )
        assert (tmp_path / "reports" / "findasbuilts_20260904_100000.csv").exists()


class TestFailingDocument:
    def test_one_failing_doc_becomes_error_row_without_abort(self, monkeypatch, tmp_path):
        doc_ok = tmp_path / "some/dir" / "ok.pdf"
        doc_bad = tmp_path / "some/dir" / "bad.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))

        def extract(path, ocr_settings):
            if path == str(doc_bad):
                raise RuntimeError("corrupt file")
            return _text_layer_result("P.E. No. 123456")

        _run_main(
            monkeypatch, tmp_path,
            documents=[doc_ok, doc_bad], extract=extract, clock=clock,
        )

        rows = _latest_report_rows(tmp_path)
        assert len(rows) == 2
        by_path = {r["file_path"]: r for r in rows}
        assert by_path[str(doc_ok)]["verdict"] == "STAMP"
        assert by_path[str(doc_bad)]["verdict"] == "ERROR"
        assert by_path[str(doc_bad)]["error"] == "corrupt file"


class TestPerDocumentLogging:
    def test_error_message_in_per_document_log_line(self, monkeypatch, tmp_path, caplog):
        doc_bad = tmp_path / "some/dir" / "bad.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))

        def extract(path, ocr_settings):
            raise RuntimeError("corrupt file")

        with caplog.at_level(logging.INFO, logger="findasbuilts.cli"):
            _run_main(
                monkeypatch, tmp_path,
                documents=[doc_bad], extract=extract, clock=clock,
            )

        # The per-document line carries the error, not just the verdict.
        assert "1/1 bad.pdf -> ERROR: corrupt file" in caplog.text

    def test_stamp_line_has_no_error_suffix(self, monkeypatch, tmp_path, caplog):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))

        with caplog.at_level(logging.INFO, logger="findasbuilts.cli"):
            _run_main(
                monkeypatch, tmp_path,
                documents=[doc_a],
                extract=lambda path, s: _text_layer_result("P.E. No. 123456"),
                clock=clock,
            )

        assert "1/1 a.pdf -> STAMP" in caplog.text
        assert "-> STAMP:" not in caplog.text


class TestStartupMessaging:
    """Startup/progress output: the run announces the scan and each document
    before it is processed, so a large tree or a slow first OCR never looks
    like a hang."""

    def test_announces_scan_and_found_count(self, monkeypatch, tmp_path, caplog):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        doc_b = tmp_path / "some/dir" / "b.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))

        with caplog.at_level(logging.INFO, logger="findasbuilts.cli"):
            _run_main(
                monkeypatch, tmp_path,
                documents=[doc_a, doc_b],
                extract=lambda path, s: _text_layer_result("P.E. No. 123456"),
                clock=clock,
            )

        assert "Scanning some/dir for documents ..." in caplog.text
        assert (
            f"Found 2 document(s); report -> "
            f"{os.path.join('reports', 'findasbuilts_20260904_100000.csv')}"
            in caplog.text
        )

    def test_processing_line_precedes_result_line(self, monkeypatch, tmp_path, caplog):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        doc_b = tmp_path / "some/dir" / "b.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))

        with caplog.at_level(logging.INFO, logger="findasbuilts.cli"):
            _run_main(
                monkeypatch, tmp_path,
                documents=[doc_a, doc_b],
                extract=lambda path, s: _text_layer_result("P.E. No. 123456"),
                clock=clock,
            )

        # The worker logs "Processing <name> ..." at its actual start; the
        # main thread logs the completion line. Per-file ordering holds for
        # any completion order (the worker line always precedes its own
        # verdict line).
        assert "Processing a.pdf ..." in caplog.text
        assert "Processing b.pdf ..." in caplog.text
        assert (
            caplog.text.index("Processing a.pdf ...")
            < caplog.text.index("a.pdf -> STAMP")
        )
        assert (
            caplog.text.index("Processing b.pdf ...")
            < caplog.text.index("b.pdf -> STAMP")
        )

    def test_empty_dir_still_announces_scan(self, monkeypatch, tmp_path, caplog):
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))

        with caplog.at_level(logging.INFO, logger="findasbuilts.cli"):
            _run_main(
                monkeypatch, tmp_path,
                documents=[], extract=lambda path, s: _text_layer_result("x"),
                clock=clock,
            )

        assert "Scanning some/dir for documents ..." in caplog.text
        assert "0 documents found" in caplog.text


class TestTesseractMissing:
    def test_warns_once_and_marks_ocr_docs_error_but_text_layer_ok(
        self, monkeypatch, tmp_path, caplog
    ):
        doc_scan1 = tmp_path / "some/dir" / "scan1.pdf"
        doc_scan2 = tmp_path / "some/dir" / "scan2.pdf"
        doc_text = tmp_path / "some/dir" / "text.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))

        def extract(path, ocr_settings):
            if path in (str(doc_scan1), str(doc_scan2)):
                raise TesseractNotFoundError()
            return _text_layer_result("P.E. No. 123456")

        with caplog.at_level(logging.WARNING, logger="findasbuilts.cli"):
            _run_main(
                monkeypatch, tmp_path,
                documents=[doc_scan1, doc_scan2, doc_text], extract=extract, clock=clock,
            )

        # Warning logged exactly once, not once per failing document.
        assert caplog.text.count("Tesseract binary not found") == 1

        rows = _latest_report_rows(tmp_path)
        assert len(rows) == 3
        by_path = {r["file_path"]: r for r in rows}
        assert by_path[str(doc_scan1)]["verdict"] == "ERROR"
        assert by_path[str(doc_scan1)]["error"] == "OCR unavailable: Tesseract not found"
        assert by_path[str(doc_scan2)]["verdict"] == "ERROR"
        assert by_path[str(doc_scan2)]["error"] == "OCR unavailable: Tesseract not found"
        # Text-layer-only doc is unaffected.
        assert by_path[str(doc_text)]["verdict"] == "STAMP"


class TestEmptyDirectory:
    def test_empty_dir_writes_empty_csv_and_exits_0(self, monkeypatch, tmp_path, capsys, caplog):
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))

        with caplog.at_level(logging.WARNING, logger="findasbuilts.cli"):
            _run_main(
                monkeypatch, tmp_path,
                documents=[], extract=lambda path, s: _text_layer_result("x"), clock=clock,
            )

        assert "0 documents found" in caplog.text
        rows = _latest_report_rows(tmp_path)
        assert rows == []
        out = capsys.readouterr().out
        assert "Scanned 0 document(s)" in out


class TestMinCharsOverride:
    def test_min_chars_reaches_extraction(self, monkeypatch, tmp_path):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        seen = {}

        def extract(path, ocr_settings):
            seen["min_chars"] = ocr_settings["min_chars_for_text_layer"]
            return _text_layer_result("LICENSE NO 12345")

        _run_main(
            monkeypatch, tmp_path,
            documents=[doc_a], extract=extract, clock=clock,
            argv_extra=["--min-chars", "50"],
        )
        assert seen["min_chars"] == 50


class TestMissingDirectory:
    def test_nonexistent_directory_exits_2(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli, "load_config_with_local", lambda path: _valid_config())
        monkeypatch.setattr(cli, "validate_config", lambda config: None)
        monkeypatch.setattr(sys, "argv", ["findasbuilts", "does-not-exist"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2


class TestResume:
    """--resume: skip documents already in the resume report, carry their
    rows forward, and write a new complete report. The resume file lives at
    tmp_path/resume.csv (outside reports/) so it never collides with
    _latest_report_rows' glob."""

    def _stamp_row(self, path):
        return verdict_to_row(Verdict(
            path=path, has_stamp=True, verdict="STAMP",
            stamp_keywords=["p.e."], stamp_pages={"p.e.": [1]},
            page_count=1, text_source="text_layer",
            evidence="P.E. No. 123456", error="",
        ))

    def test_resume_skips_processed_and_writes_complete_report(
        self, monkeypatch, tmp_path, capsys
    ):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        doc_b = tmp_path / "some/dir" / "b.pdf"
        resume_path = tmp_path / "resume.csv"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        write_report([self._stamp_row(str(doc_a))], str(resume_path))

        called = []

        def extract(path, ocr_settings):
            called.append(path)
            return _text_layer_result("plain prose about a water system")

        _run_main(
            monkeypatch, tmp_path,
            documents=[doc_a, doc_b], extract=extract, clock=clock,
            argv_extra=["--resume", str(resume_path)],
        )

        # doc_a was already in the resume report -- not re-extracted.
        assert str(doc_a) not in called
        assert str(doc_b) in called

        # The new report is complete: carried STAMP row + new NO_STAMP row.
        rows = _latest_report_rows(tmp_path)
        assert len(rows) == 2
        by_path = {r["file_path"]: r for r in rows}
        assert by_path[str(doc_a)]["verdict"] == "STAMP"
        assert by_path[str(doc_a)]["stamp_keywords"] == "p.e."
        assert by_path[str(doc_b)]["verdict"] == "NO_STAMP"

        out = capsys.readouterr().out
        assert "Scanned 2 document(s) in some/dir" in out
        assert f"Resumed from {resume_path}: skipped 1 document(s)" in out
        assert "Stamped (1):" in out
        assert "Not stamped (1):" in out

    def test_missing_resume_file_exits_2(self, monkeypatch, tmp_path):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        with pytest.raises(SystemExit) as exc:
            _run_main(
                monkeypatch, tmp_path,
                documents=[doc_a],
                extract=lambda path, s: _text_layer_result("P.E. No. 123456"),
                clock=clock,
                argv_extra=["--resume", str(tmp_path / "does-not-exist.csv")],
            )
        assert exc.value.code == 2

    def test_all_already_processed_warns_and_writes_carried_rows(
        self, monkeypatch, tmp_path, caplog, capsys
    ):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        doc_b = tmp_path / "some/dir" / "b.pdf"
        resume_path = tmp_path / "resume.csv"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        write_report([
            self._stamp_row(str(doc_a)),
            verdict_to_row(Verdict(
                path=str(doc_b), has_stamp=False, verdict="NO_STAMP",
                stamp_keywords=[], stamp_pages={},
                page_count=1, text_source="text_layer", evidence="", error="",
            )),
        ], str(resume_path))

        called = []

        def extract(path, ocr_settings):
            called.append(path)
            return _text_layer_result("P.E. No. 123456")

        with caplog.at_level(logging.WARNING, logger="findasbuilts.cli"):
            _run_main(
                monkeypatch, tmp_path,
                documents=[doc_a, doc_b], extract=extract, clock=clock,
                argv_extra=["--resume", str(resume_path)],
            )

        assert "nothing to process" in caplog.text
        assert called == []
        rows = _latest_report_rows(tmp_path)
        assert len(rows) == 2
        by_path = {r["file_path"]: r for r in rows}
        assert by_path[str(doc_a)]["verdict"] == "STAMP"
        assert by_path[str(doc_b)]["verdict"] == "NO_STAMP"
        out = capsys.readouterr().out
        assert "Scanned 2 document(s)" in out
        assert f"Resumed from {resume_path}: skipped 2 document(s)" in out

    def test_empty_resume_file_processes_everything(self, monkeypatch, tmp_path):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        resume_path = tmp_path / "resume.csv"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        write_report([], str(resume_path))

        called = []

        def extract(path, ocr_settings):
            called.append(path)
            return _text_layer_result("P.E. No. 123456")

        _run_main(
            monkeypatch, tmp_path,
            documents=[doc_a], extract=extract, clock=clock,
            argv_extra=["--resume", str(resume_path)],
        )
        assert called == [str(doc_a)]
        rows = _latest_report_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["file_path"] == str(doc_a)
        assert rows[0]["verdict"] == "STAMP"

    def test_empty_dir_with_resume_still_exits_0(self, monkeypatch, tmp_path, capsys):
        # The empty-dir check runs before the resume file is read, so a
        # missing resume file is not an error when there is nothing to scan.
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        _run_main(
            monkeypatch, tmp_path,
            documents=[], extract=lambda path, s: _text_layer_result("x"),
            clock=clock,
            argv_extra=["--resume", str(tmp_path / "does-not-exist.csv")],
        )
        rows = _latest_report_rows(tmp_path)
        assert rows == []
        out = capsys.readouterr().out
        assert "Scanned 0 document(s)" in out

    def test_processing_line_counts_pending_not_total(self, monkeypatch, tmp_path, caplog):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        doc_b = tmp_path / "some/dir" / "b.pdf"
        resume_path = tmp_path / "resume.csv"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        write_report([self._stamp_row(str(doc_a))], str(resume_path))

        with caplog.at_level(logging.INFO, logger="findasbuilts.cli"):
            _run_main(
                monkeypatch, tmp_path,
                documents=[doc_a, doc_b],
                extract=lambda path, s: _text_layer_result("P.E. No. 123456"),
                clock=clock,
                argv_extra=["--resume", str(resume_path)],
            )

        # doc_a is carried forward; only doc_b is processed, so progress is
        # 1/1 -- the denominator is the remaining work, not the total. The
        # worker's "Processing" line no longer carries i/n, so the
        # denominator is asserted on the completion line instead.
        assert "Processing b.pdf ..." in caplog.text
        assert "Processing a.pdf" not in caplog.text
        assert "1/1 b.pdf -> STAMP" in caplog.text
        assert "1/2" not in caplog.text


class _RecordingExecutor:
    """Wraps a real ThreadPoolExecutor, recording the max_workers it was
    created with so tests can assert the jobs resolution without racing
    threads."""

    def __init__(self, max_workers=None):
        self.max_workers = max_workers
        self._inner = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, fn, *args, **kwargs):
        return self._inner.submit(fn, *args, **kwargs)

    def shutdown(self, wait=True, cancel_futures=False):
        self._inner.shutdown(wait=wait, cancel_futures=cancel_futures)


class TestJobs:
    """--jobs resolution: flag > config 'jobs' > min(CPU count, 4)."""

    def _run_with_recording_executor(self, monkeypatch, tmp_path, *, documents, clock, argv_extra=None, config=None):
        recorded = {}

        def recording_factory(max_workers=None):
            recorded["max_workers"] = max_workers
            return _RecordingExecutor(max_workers=max_workers)

        monkeypatch.setattr(cli, "ThreadPoolExecutor", recording_factory)
        _run_main(
            monkeypatch, tmp_path,
            documents=documents,
            extract=lambda path, s: _text_layer_result("P.E. No. 123456"),
            clock=clock,
            argv_extra=argv_extra,
            config=config,
        )
        return recorded["max_workers"]

    def test_jobs_zero_exits_2(self, monkeypatch, tmp_path):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        with pytest.raises(SystemExit) as exc:
            _run_main(
                monkeypatch, tmp_path,
                documents=[doc_a],
                extract=lambda path, s: _text_layer_result("P.E. No. 123456"),
                clock=clock,
                argv_extra=["--jobs", "0"],
            )
        assert exc.value.code == 2

    def test_jobs_flag_reaches_executor(self, monkeypatch, tmp_path):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        workers = self._run_with_recording_executor(
            monkeypatch, tmp_path,
            documents=[doc_a], clock=clock, argv_extra=["--jobs", "3"],
        )
        assert workers == 3

    def test_jobs_defaults_to_config(self, monkeypatch, tmp_path):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        workers = self._run_with_recording_executor(
            monkeypatch, tmp_path,
            documents=[doc_a], clock=clock, config=_valid_config(jobs=3),
        )
        assert workers == 3

    def test_jobs_flag_overrides_config(self, monkeypatch, tmp_path):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        workers = self._run_with_recording_executor(
            monkeypatch, tmp_path,
            documents=[doc_a], clock=clock,
            config=_valid_config(jobs=1), argv_extra=["--jobs", "4"],
        )
        assert workers == 4

    def test_jobs_defaults_to_cpu_count_capped(self, monkeypatch, tmp_path):
        # 16 logical processors must NOT become 16 concurrent readers on a
        # network share -- the auto-detect default is capped at 4.
        doc_a = tmp_path / "some/dir" / "a.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        monkeypatch.setattr(cli.os, "cpu_count", lambda: 16)
        workers = self._run_with_recording_executor(
            monkeypatch, tmp_path, documents=[doc_a], clock=clock,
        )
        assert workers == 4

    def test_jobs_defaults_to_cpu_count_below_cap(self, monkeypatch, tmp_path):
        # The cap only kicks in above 4 -- a 2-core machine gets 2 workers.
        doc_a = tmp_path / "some/dir" / "a.pdf"
        clock = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        monkeypatch.setattr(cli.os, "cpu_count", lambda: 2)
        workers = self._run_with_recording_executor(
            monkeypatch, tmp_path, documents=[doc_a], clock=clock,
        )
        assert workers == 2

    def test_jobs_1_and_2_produce_same_rows(self, monkeypatch, tmp_path):
        doc_a = tmp_path / "some/dir" / "a.pdf"
        doc_b = tmp_path / "some/dir" / "b.pdf"
        doc_c = tmp_path / "some/dir" / "c.pdf"
        documents = [doc_a, doc_b, doc_c]

        def extract(path, ocr_settings):
            if path == str(doc_a):
                return _text_layer_result("P.E. No. 123456")
            return _text_layer_result("plain prose about a water system")

        # jobs=1 run (deterministic completion order).
        clock1 = _Clock(datetime(2026, 9, 4, 10, 0, 0))
        _run_main(
            monkeypatch, tmp_path,
            documents=documents, extract=extract, clock=clock1,
            argv_extra=["--jobs", "1"],
        )
        rows1 = _latest_report_rows(tmp_path)

        # jobs=2 run (non-deterministic completion order) -- different clock
        # so the second report doesn't overwrite the first.
        clock2 = _Clock(datetime(2026, 9, 4, 10, 0, 1))
        _run_main(
            monkeypatch, tmp_path,
            documents=documents, extract=extract, clock=clock2,
            argv_extra=["--jobs", "2"],
        )
        rows2 = _latest_report_rows(tmp_path)

        by_path1 = {r["file_path"]: r for r in rows1}
        by_path2 = {r["file_path"]: r for r in rows2}
        assert set(by_path1) == set(by_path2)
        for path in by_path1:
            assert by_path1[path]["verdict"] == by_path2[path]["verdict"]
