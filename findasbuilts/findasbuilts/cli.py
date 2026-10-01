"""Command-line orchestration entry point for the findasbuilts tool.

Wires together the pure-logic and I/O modules in ``findasbuilts/`` into a
single runnable workflow: scan a directory for as-built documents, extract
text per page (text layer with OCR fallback), match the configured stamp
keyword list, and emit a verdict per document -- ``STAMP``, ``NO_STAMP``,
or ``ERROR`` -- as a console listing plus a timestamped CSV report.

This module intentionally contains no extraction/matching logic itself --
it only sequences calls into the other ``findasbuilts`` modules.
"""

import argparse
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from pytesseract import TesseractNotFoundError

from findasbuilts.config import load_config_with_local, validate_config
from findasbuilts.extract import aggregate_source, extract_text
from findasbuilts.keywords import KeywordMatch
from findasbuilts.report import (
    ReportWriter,
    normalize_row,
    read_report,
    verdict_to_row,
    write_report,
)
from findasbuilts.scan import iter_documents
from findasbuilts.stamp import detect_stamp
from findasbuilts.verdict import Verdict, evaluate_document

logger = logging.getLogger(__name__)

# Shared by the worker (which builds the ERROR verdict) and the main thread
# (which warns once per run when it sees this error string on a completion),
# so the two never drift.
OCR_UNAVAILABLE_ERROR = "OCR unavailable: Tesseract not found"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="findasbuilts",
        description="Scan a directory of as-built documents for an engineer's stamp.",
    )
    parser.add_argument(
        "directory",
        help="Directory to scan for as-built documents.",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the config JSON file (default: config.json).",
    )
    parser.add_argument(
        "--report-dir",
        default="reports",
        help="Directory to write report CSVs (default: reports).",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=None,
        help="Override ocr.min_chars_for_text_layer for this run (must be >= 1).",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Number of parallel worker threads (default: config 'jobs', or "
        "min(CPU count, 4)).",
    )
    parser.add_argument(
        "--resume",
        default=None,
        metavar="REPORT_CSV",
        help="Resume from a prior report CSV: skip documents already present "
        "and write a new complete report.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable DEBUG logging.",
    )
    return parser


def _row_to_verdict(row: dict) -> Verdict:
    """Reconstruct a Verdict from a CSV report row.

    Used to show carried-forward resume rows in the console summary, which
    consumes ``list[Verdict]``. The reconstruction is lossless for the
    fields the console reads (``path``, ``verdict``, ``error``); the
    ``if ... else`` guards handle empty-string cells.
    """
    return Verdict(
        path=row["file_path"],
        has_stamp=row["verdict"] == "STAMP",
        verdict=row["verdict"],
        stamp_keywords=row["stamp_keywords"].split(";") if row["stamp_keywords"] else [],
        stamp_pages=json.loads(row["stamp_pages"]) if row["stamp_pages"] else {},
        page_count=int(row["page_count"]) if row["page_count"] else 0,
        text_source=row["text_source"],
        evidence=row["evidence"],
        error=row["error"],
    )


def _print_console(verdicts, scanned_dir, report_path, resumed_from=None, skipped=0) -> None:
    """Print the human-readable console summary: Stamped / Not stamped /
    Errors groups, then the report path.

    When ``resumed_from`` is set (a --resume run), a line noting how many
    documents were skipped is printed after the scan count. Uses print (not
    logger.info): the console listing is the tool's primary human output,
    meant to be read at the terminal.
    """
    stamped = [v for v in verdicts if v.verdict == "STAMP"]
    not_stamped = [v for v in verdicts if v.verdict == "NO_STAMP"]
    errors = [v for v in verdicts if v.verdict == "ERROR"]

    print(f"Scanned {len(verdicts)} document(s) in {scanned_dir}")
    if resumed_from:
        print(f"Resumed from {resumed_from}: skipped {skipped} document(s)")
    if stamped:
        print(f"Stamped ({len(stamped)}):")
        for v in stamped:
            print(f"  - {v.path}")
    if not_stamped:
        print(f"Not stamped ({len(not_stamped)}):")
        for v in not_stamped:
            print(f"  - {v.path}")
    if errors:
        print(f"Errors ({len(errors)}):")
        for v in errors:
            print(f"  - {v.path}: {v.error}")
    print(f"Report: {report_path}")


def _process_document(path, ocr_settings, stamp_keywords, extraction_settings) -> Verdict:
    """Extract -> detect -> evaluate one document, returning a Verdict.

    Runs in a worker thread (see main's ThreadPoolExecutor). All exceptions
    are converted to ERROR verdicts so one bad document never aborts the
    run; TesseractNotFoundError is distinguished by its error string so the
    main thread can warn once per run.
    """
    logger.info("Processing %s ...", path.name)
    try:
        result = extract_text(str(path), ocr_settings, extraction_settings)
        stamp = detect_stamp(result.text_by_page, stamp_keywords)
        text_source = aggregate_source(result.source_by_page)
        return evaluate_document(
            str(path),
            stamp,
            page_count=result.page_count,
            text_source=text_source,
        )
    except TesseractNotFoundError:
        return Verdict(
            path=str(path),
            has_stamp=False,
            verdict="ERROR",
            stamp_keywords=[],
            stamp_pages={},
            page_count=0,
            text_source="",
            evidence="",
            error=OCR_UNAVAILABLE_ERROR,
        )
    except Exception as exc:
        return Verdict(
            path=str(path),
            has_stamp=False,
            verdict="ERROR",
            stamp_keywords=[],
            stamp_pages={},
            page_count=0,
            text_source="",
            evidence="",
            error=str(exc),
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # os.walk on a nonexistent path silently yields nothing, indistinguishable
    # from an empty dir -- check explicitly so a typo'd DIRECTORY is a usage
    # error (exit 2), not a confusing "0 documents found" run.
    if not os.path.isdir(args.directory) or not os.access(args.directory, os.R_OK):
        parser.error(f"directory does not exist or is not readable: {args.directory}")

    # Config errors (FileNotFoundError / JSONDecodeError / ValueError) are
    # deliberately NOT caught -- they propagate (exit 1), sibling parity.
    # load_config_with_local overlays a sibling <name>.local.json (e.g.
    # config.local.json) over the base config, so machine-specific settings
    # like a Tesseract path are picked up without a --config flag.
    config = load_config_with_local(args.config)
    validate_config(config)

    # argparse's type=int alone lets 0/-5 bypass validate_config's
    # positive-int rule, so --min-chars is validated here.
    if args.min_chars is not None and args.min_chars < 1:
        parser.error("--min-chars must be >= 1")
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be >= 1")

    ocr_settings = dict(config["ocr"])
    if args.min_chars is not None:
        ocr_settings["min_chars_for_text_layer"] = args.min_chars

    stamp_keywords = config["keywords"]["stamp"]
    extraction_settings = config.get("extraction") or {}

    # --jobs flag > config 'jobs' key > conservative auto-detect. The
    # min(..., 4) cap is deliberate: 16 concurrent readers on an SMB share
    # would hit rate limits, so a many-core machine doesn't default to
    # hammering the network. Raise it via --jobs or config 'jobs' for local
    # disks.
    jobs = args.jobs if args.jobs is not None else config.get("jobs")
    if jobs is None:
        jobs = min(os.cpu_count() or 1, 4)

    report_path = os.path.join(
        args.report_dir, f"findasbuilts_{datetime.now():%Y%m%d_%H%M%S}.csv"
    )

    # Startup progress: the scan and the first document's extraction are the
    # two silent stretches of a run -- announce them so a large tree or a
    # slow first OCR doesn't look like a hang.
    logger.info("Scanning %s for documents ...", args.directory)
    documents = list(iter_documents(args.directory, config["file_extensions"]))
    if not documents:
        logger.warning("0 documents found in %s", args.directory)
        write_report([], report_path)
        _print_console([], args.directory, report_path)
        return
    logger.info("Found %d document(s); report -> %s", len(documents), report_path)

    # --resume: read the prior report, skip documents already present, and
    # carry their rows forward into the new report so it stays complete.
    # OSError covers a missing/unreadable file; ValueError covers a report
    # without a file_path column -- both are usage errors (exit 2).
    resume_rows = []
    if args.resume:
        try:
            resume_rows = read_report(args.resume)
        except (OSError, ValueError):
            parser.error(f"resume report does not exist or is not readable: {args.resume}")
        resume_rows = [normalize_row(r) for r in resume_rows]

    resume_paths = {r["file_path"] for r in resume_rows}
    pending = [p for p in documents if str(p) not in resume_paths]

    if not pending:
        logger.warning(
            "nothing to process: all %d document(s) already in %s",
            len(documents), args.resume,
        )
        write_report(resume_rows, report_path)
        _print_console(
            [_row_to_verdict(r) for r in resume_rows],
            args.directory, report_path,
            resumed_from=args.resume, skipped=len(documents) - len(pending),
        )
        return

    # Carried resume rows come first so the console shows the complete
    # picture; progress (i/n) counts the remaining work, not all documents.
    verdicts = [_row_to_verdict(r) for r in resume_rows]
    tesseract_warned = False
    n = len(pending)
    # Write the report incrementally, flushing after every row, so an
    # interrupted run still leaves a usable report behind (see ReportWriter).
    with ReportWriter(report_path) as report:
        for r in resume_rows:
            report.append(r)
        # Document-level parallelism: each worker thread processes one whole
        # document (extract -> detect -> evaluate) before taking the next.
        # Threads, not processes -- the heavy work (Tesseract subprocess via
        # pytesseract, PyMuPDF get_pixmap render) releases the GIL, and
        # threads avoid Windows pickling issues. The ReportWriter is touched
        # only from this main thread, preserving the incremental flush that
        # makes interrupted runs resumable.
        executor = ThreadPoolExecutor(max_workers=jobs)
        try:
            futures = {
                executor.submit(
                    _process_document, path, ocr_settings, stamp_keywords, extraction_settings
                ): path
                for path in pending
            }
            for i, future in enumerate(as_completed(futures), start=1):
                path = futures[future]
                verdict = future.result()
                # Warn once per run, not once per document -- without this, a
                # missing binary produces N identical opaque ERROR rows. Runs
                # in the main thread, sequentially over completions, so there
                # is no race on tesseract_warned.
                if verdict.error == OCR_UNAVAILABLE_ERROR and not tesseract_warned:
                    logger.warning(
                        "Tesseract binary not found; pages requiring OCR cannot be extracted"
                    )
                    tesseract_warned = True
                # INFO floor so a 200-doc OCR run shows life without
                # --verbose. Include the error message so a mid-run glance at
                # the console shows why a document failed, not just that it
                # failed.
                if verdict.error:
                    logger.info(
                        "%d/%d %s -> %s: %s",
                        i, n, path.name, verdict.verdict, verdict.error,
                    )
                else:
                    logger.info("%d/%d %s -> %s", i, n, path.name, verdict.verdict)
                verdicts.append(verdict)
                report.append(verdict_to_row(verdict))
        except BaseException:
            # Ctrl+C or an unexpected main-thread exception (e.g. a full disk
            # on report.append): cancel queued futures and return immediately
            # so the ReportWriter closes and the partial report is preserved.
            # In-flight OCR threads (up to jobs) finish in the background.
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            # Required on the normal path -- without it, worker threads stay
            # alive and the interpreter hangs on exit.
            executor.shutdown(wait=True)

    _print_console(
        verdicts, args.directory, report_path,
        resumed_from=args.resume, skipped=len(documents) - len(pending),
    )


if __name__ == "__main__":
    main()
