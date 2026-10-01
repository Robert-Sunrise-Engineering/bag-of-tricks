# Reference

## Architecture overview

`findasbuilts` is a single-pass pipeline: **scan** a directory for
candidate documents → **extract** per-page text (text layer with OCR
fallback) → **detect** stamp keywords → **evaluate** a verdict per document
→ **report** as a console listing plus a timestamped CSV.

```
findasbuilts_main.py            entry-point shim -> cli.main()
findasbuilts/
  cli.py                        orchestration: args, config, loop, console
  scan.py                       iter_documents: walk + extension filter
  extract.py                    extract_text: PDF text-layer/OCR (image path dormant)
  keywords.py                   match_keywords + match_stamp_cluster: the regex correctness core
  stamp.py                      detect_stamp: what "engineer's stamp" means
  verdict.py                    evaluate_document: STAMP / NO_STAMP
  report.py                     verdict_to_row + write_report (CSV)
  config.py                     load_config + validate_config
```

The pipeline is deliberately layered so the pure decision logic
(`keywords`, `stamp`, `verdict`) has no I/O, and the I/O modules
(`extract`, `scan`, `report`) have no decision logic. `cli.py` sequences
calls and owns the ERROR verdicts.

## findasbuilts/cli.py — command-line orchestration

- `_build_arg_parser() -> argparse.ArgumentParser` — `prog="findasbuilts"`;
  positional `directory`; `--config` (default `config.json`); `--report-dir`
  (default `reports`); `--min-chars` (int, default None); `--jobs` (int,
  default None); `--resume` (default None); `--verbose` (store_true). Missing
  `directory` → argparse exits 2.
- `_row_to_verdict(row: dict) -> Verdict` — reconstruct a Verdict from a CSV
  report row (for console display of carried-forward resume rows):
  `stamp_keywords` split on `;`, `stamp_pages` via `json.loads`, `page_count`
  via `int`, `has_stamp = verdict == "STAMP"`; `evidence` carried through;
  empty-string cells guarded.
- `_print_console(verdicts, scanned_dir, report_path, resumed_from=None,
  skipped=0) -> None` — prints `Scanned N document(s) in <dir>`, then (when
  `resumed_from` is set) `Resumed from <path>: skipped M document(s)`, then
  `Stamped (N):` / `Not stamped (N):` / `Errors (N):` groups (`  - {path}`,
  errors also `: {error}`), then `Report: {report_path}`. Uses `print`, not
  logging — this is the tool's primary human output.
- `_process_document(path, ocr_settings, stamp_keywords) -> Verdict` — the
  worker function run in a `ThreadPoolExecutor` thread: `extract_text` →
  `detect_stamp` → `aggregate_source` → `evaluate_document`. All exceptions
  are converted to ERROR verdicts so one bad document never aborts the run;
  `TesseractNotFoundError` is distinguished by its error string
  (`OCR_UNAVAILABLE_ERROR`) so the main thread can warn once per run. Logs
  `Processing <name> ...` at its actual start (no `i/n` — the worker doesn't
  know its index; the completion line carries it). References the pipeline
  functions as module globals, so test monkeypatches of `cli.extract_text`
  etc. keep working.
- `main() -> None` — `logging.basicConfig(level=INFO, ...)` first line;
  parse args; `--verbose` → DEBUG; explicit `os.path.isdir`/`os.access`
  check on `directory` → `parser.error` (exit 2); `load_config_with_local`
  + `validate_config` (errors propagate, exit 1); `--min-chars < 1` or
  `--jobs < 1` → `parser.error`; build `ocr_settings` (with `--min-chars`
  override); resolve `jobs` = `--jobs` flag > config `jobs` key >
  `min(os.cpu_count() or 1, 4)` (the conservative auto-detect cap — 16
  concurrent readers on an SMB share would hit rate limits); report path
  `reports/findasbuilts_<YYYYmmdd_HHMMSS>.csv`; log
  `Scanning <dir> for documents ...`, enumerate documents, log
  `Found N document(s); report -> <path>`; empty → warn `0 documents
  found`, write empty CSV, print console, return; **resume block** (only
  when `--resume` is set):
  `read_report` the resume file (`OSError`/`ValueError` → `parser.error`,
  exit 2), `normalize_row` each row, build `resume_paths` (a set of
  `file_path` values) and `pending = [p for p in documents if str(p) not in
  resume_paths]`; if `pending` is empty → warn `nothing to process`, write a
  report with the carried rows only, print console, return (exit 0);
  **document-level parallelism**: a `ThreadPoolExecutor(max_workers=jobs)`
  submits one `_process_document` future per pending document (threads, not
  processes — the heavy work, Tesseract subprocess + PyMuPDF render, releases
  the GIL, and threads avoid Windows pickling). The main thread iterates
  `as_completed(futures)` and, per completion: warns **once** per run when
  `verdict.error == OCR_UNAVAILABLE_ERROR` (the `TesseractNotFoundError`
  marker — no race, the check runs sequentially in the main thread), logs
  `INFO %d/%d <name> -> <verdict>` (denominator is `len(pending)`, the
  remaining work; error message appended as `-> ERROR: <error>`), appends the
  verdict, and writes the row. `ReportWriter` is touched only from the main
  thread (carried resume rows first, then completions), preserving the
  incremental flush that makes interrupted runs resumable. On `BaseException`
  (Ctrl+C or an unexpected main-thread error like a full disk on
  `report.append`): `executor.shutdown(wait=False, cancel_futures=True)` then
  re-raise, so the `ReportWriter` closes and the partial report is preserved
  (in-flight OCR threads, up to `jobs`, finish in the background). On the
  normal path: `executor.shutdown(wait=True)` — required, or worker threads
  keep the interpreter alive on exit. Then `_print_console` with
  `resumed_from=args.resume` and `skipped=len(documents)-len(pending)`.

**Exit codes:** 0 = run completed (even with NO_STAMP/ERROR rows); 2 =
usage error (missing/unreadable directory, `--min-chars < 1`, `--jobs < 1`,
missing/unreadable resume file); 1 = config load/validation failure
(propagated).

## findasbuilts/scan.py — directory scanning

- `iter_documents(root, extensions) -> Iterator[Path]` — `os.walk`, skips
  `.`-prefixed directories, yields files whose lowercased suffix is in
  `extensions`, sorted by absolute path. Nonexistent `root` yields nothing
  (cli checks for a missing directory explicitly and exits 2 first).

## findasbuilts/extract.py — text extraction with OCR fallback

- `extract_text(path, ocr_settings) -> ExtractionResult` — dispatches on
  `.pdf` extension. **Raises:** `ValueError` (encrypted/zero-page PDF);
  `pytesseract.pytesseract.TesseractNotFoundError` (OCR needed but binary
  missing — deliberately NOT caught here; propagates to cli.py).
- `_extract_pdf(path, ocr_settings) -> ExtractionResult` — per page:
  `page.get_text()`; if `_needs_ocr` → render at `ocr_dpi`, round-trip the
  pixmap through PNG + `PIL.Image.open(...).convert("RGB")`, OCR, and
  **replace** the sparse text-layer text; source `"ocr"` vs `"text_layer"`.
  `page_count` captured inside the `with fitz.open(...)` block.
- `_extract_image(path, ocr_settings) -> ExtractionResult` — every frame of
  `PIL.ImageSequence.Iterator` is OCRed (multi-frame TIFFs fully covered);
  source `"image_ocr"`; `page_count` = number of frames. **Dormant in v1:**
  the default `file_extensions` is `[".pdf"]`, so the scan never yields a
  non-PDF and this path is unreachable from the CLI. It is kept (and tested)
  so adding extensions back to `file_extensions` re-enables it with no code
  change.
- `_needs_ocr(text, min_chars) -> bool` — `len(text.strip()) < min_chars`.
- `aggregate_source(source_by_page) -> str` — all pages share one source →
  that value; else `"mixed"`.
- `_configure_tesseract(ocr_settings) -> None` — sets
  `pytesseract.pytesseract.tesseract_cmd` when `ocr.tesseract_cmd` is set.
- `_tesseract_config(ocr_settings) -> str` — `f"--psm {psm}"` when
  `ocr.tesseract_psm` is set, else `""`.

## findasbuilts/keywords.py — the regex correctness core

- `_compile_keyword_pattern(keyword) -> re.Pattern` — the shared
  whole-word-ish, case-insensitive regex
  `(?<![A-Za-z0-9])<escaped>(?:s)?(?![A-Za-z0-9])` used by both matchers.
- `match_keywords(text_by_page, keywords) -> KeywordMatch` — compiles each
  keyword once via `_compile_keyword_pattern`; returns matched keywords in
  config order (deduped), their 1-based pages, and the count. Overlapping
  keywords both appear — `matched` is an evidence inventory, not a
  best-match selection.
- `KeywordMatch` (frozen dataclass) — `matched: list[str]`, `pages:
  dict[str, list[int]]`, `count: int`.
- `LICENSE_NUMBER_PATTERN` — a PE license number: a `Utah`/`State of Utah`
  prefix, or a `license`/`lic.` prefix not preceded by a word (rejects
  `Bidder's License`), or a bare `no.`/`number` not preceded by a word
  (rejects `Project No`), then `no.`/`number`/`#`, then 6-8 digits
  (IGNORECASE). Matches `State of Utah No. 12338863`, `Utah No. 4777017-2202`,
  `License #12338863`, `No. 12338863`. The `#` form requires an explicit
  `State of Utah` prefix — bare `Utah #12345` is bank-account style (a
  text-layer line break in "...State Bank of Southern\nUtah #5110788" would
  otherwise let the account number match near a signature's "P.E."). The
  context requirement and the 6-8 digit floor reject water-right numbers
  (`APPLICATION/CLAIM NO.: A24354` — letters), 4-digit contract numbers
  (`Work Release No. 2020`), water-system numbers (`System #13060`), project
  numbers (`Project No: 220638`), file numbers (`File #15254`),
  bidder's/operator's licenses, and bank accounts (`#5110788`).
- `DEFAULT_MAX_CLUSTER_DISTANCE = 250` — the default designation↔license
  proximity window (chars). 250, not 300, because a contract-number false
  positive ("Work Release No. 2020" near a signature block's "P.E.") sits
  at ~296 chars on the KCWCD corpus.
- `match_stamp_cluster(text_by_page, designations, max_distance=250) ->
  StampCluster | None` — the stamp-specific matcher. For each page in
  order: find designation positions (via `_compile_keyword_pattern`) and
  license positions (via `LICENSE_NUMBER_PATTERN`), and return the closest
  designation↔license pair within `max_distance` on the earliest such page.
  `snippet` = ~80 chars either side of the cluster, newlines collapsed to
  `" | "`.
- `StampCluster` (frozen dataclass) — `designation: str`, `license_number:
  str`, `page: int`, `distance: int`, `snippet: str`.

## findasbuilts/stamp.py — engineer's-stamp detection

- `detect_stamp(text_by_page, stamp_keywords) -> StampCluster | None` — thin
  semantic wrapper over `match_stamp_cluster`; owns what the keyword list
  *means* (see module docstring). A document is stamped only when the
  evidence clusters — a designation within `max_distance` chars of a license
  number on the same page. `None` → no stamp evidence.

## findasbuilts/verdict.py — the pure decision layer

- `evaluate_document(path, stamp, *, page_count, text_source) -> Verdict` —
  `STAMP` if `stamp` is a `StampCluster` else `NO_STAMP`; `error` always
  `""` (ERROR verdicts are constructed by cli.py when extraction raises).
- `Verdict` (frozen dataclass) — `path`, `has_stamp`, `verdict`,
  `stamp_keywords`, `stamp_pages`, `page_count`, `text_source`, `evidence`,
  `error`. `evidence` is the cluster snippet (empty unless `STAMP`).

## findasbuilts/report.py — CSV report writing

- `REPORT_COLUMNS` — the canonical 8-column schema in order: `file_path`,
  `verdict`, `stamp_keywords`, `stamp_pages`, `page_count`, `text_source`,
  `evidence`, `error`. `verdict_to_row` builds rows from it and
  `normalize_row` restricts resume rows to it, so a resumed report's header
  is uniform.
- `verdict_to_row(verdict) -> dict` — canonical column order (from
  `REPORT_COLUMNS`): `file_path`, `verdict`, `stamp_keywords`
  (semicolon-joined, config order), `stamp_pages` (JSON dict), `page_count`,
  `text_source`, `evidence` (the cluster snippet, empty unless `STAMP`),
  `error`.
- `normalize_row(row: dict) -> dict` — restrict a row dict to
  `REPORT_COLUMNS`, defaulting missing keys to `""`. Carried resume rows go
  through this so a resume file with extra/missing columns can't crash
  `csv.DictWriter`.
- `read_report(path) -> list[dict]` — read a report CSV into a list of row
  dicts (all values strings). Empty file → `[]`. **Raises:**
  `FileNotFoundError` (path missing); `ValueError` (header present but no
  `file_path` column).
- `write_report(rows, path) -> None` — byte-identical contract to
  `data-conflation/conflate/report.py`: union of keys in first-seen order,
  `restval=''`, empty rows → empty file, creates parent dir.
- `ReportWriter(path)` — incremental variant: writes the header on the first
  `append(row)` and flushes to disk after every row, so an interrupted run
  still leaves a usable report. Header is the first row's keys (the cli's
  `verdict_to_row` rows are uniform, so this matches `write_report`'s
  union-of-keys for that caller). `close()` / context manager releases the
  file handle.

## findasbuilts/config.py — config load + validation

- `load_config(path) -> dict` — `json.load`. **Raises:**
  `FileNotFoundError`, `json.JSONDecodeError`.
- `load_config_with_local(path) -> dict` — `load_config(path)`, then if a
  sibling `<name>.local.json` exists (e.g. `config.local.json` next to
  `config.json`), deep-merge it over the base: local wins, dicts merge
  recursively, lists/scalars are replaced. **Raises:** the same as
  `load_config`, plus `ValueError` if the local file is not a JSON object.
- `_local_config_path(path) -> str` — `config.json` → `config.local.json`.
- `_deep_merge(base, override) -> dict` — recursive dict merge (dicts
  recurse; everything else replaced by the override value).
- `validate_config(config) -> None` — raises `ValueError` naming the bad
  key. Rules: top-level `file_extensions`/`ocr`/`keywords` present;
  `file_extensions` non-empty list of `.`-prefixed strings;
  `ocr.min_chars_for_text_layer` and `ocr.ocr_dpi` ints > 0 (bool guard);
  `ocr.tesseract_lang` non-empty string; `ocr.tesseract_psm` int or None;
  `ocr.tesseract_cmd` string or None; `keywords.stamp` non-empty list of
  non-empty strings; `jobs` (optional) an int > 0 when present (bool guard).
  No `save_config` in v1.

## Artifact schemas

### CSV report (`reports/findasbuilts_<YYYYmmdd_HHMMSS>.csv`)

| column          | type   | meaning                                              |
|-----------------|--------|------------------------------------------------------|
| `file_path`     | str    | absolute path of the document                        |
| `verdict`       | str    | `STAMP` / `NO_STAMP` / `ERROR`                      |
| `stamp_keywords`| str    | matched designation, `;`-joined, config order        |
| `stamp_pages`   | str    | JSON `{designation: [page, ...]}`                    |
| `page_count`    | int    | pages/frames extracted                               |
| `text_source`   | str    | `text_layer` / `ocr` / `mixed` (`image_ocr` only if the dormant image path is re-enabled) |
| `evidence`      | str    | text snippet around the stamp cluster; empty unless `STAMP` |
| `error`         | str    | non-empty only for `ERROR` rows                      |

### Console listing

```
Scanned N document(s) in <dir>
Stamped (N):
  - <path>
Not stamped (N):
  - <path>
Errors (N):
  - <path>: <error>
Report: <report_path>
```

## Invariants

- **A stamp is a cluster, not a loose phrase.** `STAMP` requires an engineer
  designation within `max_distance` (250) characters of a license number on
  the same page. A signature block's "P.E." with no license number nearby,
  or form-instruction "license number" with no designation, is `NO_STAMP`.
- **ERROR rows are data, not run failure.** A run where everything is
  `NO_STAMP` or some docs are `ERROR` still exits 0.
- **OCR output replaces sparse text-layer text** — never appended to it.
- **`p.e.` matches at line end and before punctuation** — the trailing
  lookahead must not reject `,`, `.`, or `\n` (pinned in tests).
- **`matched` is an evidence inventory** — overlapping keywords both appear.
- **`config.json` is git-tracked; `config.local.json` is ignored.**
- **`config.local.json` is auto-merged over `config.json` when present** —
  machine-specific overrides (e.g. `ocr.tesseract_cmd`) need no `--config`.
- **`reports/` is git-ignored** (only `.gitkeep` tracked).
- **A resumed report is a complete superset** — carried-forward rows plus
  newly processed rows, written to a new timestamped file. `--resume` skips
  by exact `file_path` string match against `str(path)` from
  `iter_documents`; the resume file is expected to come from a prior run of
  the same tool on the same machine/dir.

## Config schema reference

```json
{
  "file_extensions": [".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg"],
  "ocr": {
    "min_chars_for_text_layer": 20,
    "ocr_dpi": 300,
    "tesseract_lang": "eng",
    "tesseract_psm": null,
    "tesseract_cmd": null
  },
  "keywords": {
    "stamp": [
      "professional engineer",
      "registered professional engineer",
      "licensed professional engineer",
      "p.e.",
      "pe seal",
      "engineer's seal",
      "engineer seal",
      "registered engineer",
      "licensed engineer",
      "certificate of authorization"
    ]
  }
}
```

| key | type | notes |
|-----|------|-------|
| `file_extensions` | list[str] | `.`-prefixed suffixes, lowercased at match time; v1 default is `[".pdf"]` |
| `ocr.min_chars_for_text_layer` | int > 0 | below this, a page is OCRed |
| `ocr.ocr_dpi` | int > 0 | render DPI for OCR fallback |
| `ocr.tesseract_lang` | str | Tesseract language pack |
| `ocr.tesseract_psm` | int \| null | passed as `--psm <n>`; 11 helps sparse sheets |
| `ocr.tesseract_cmd` | str \| null | machine-specific Tesseract binary path |
| `keywords.stamp` | list[str] | engineer designations; a `STAMP` requires one within 250 chars of a license number on the same page |
| `jobs` | int > 0, optional | parallel worker threads; absent/None → `min(CPU count, 4)`. Not in `config.json` by default (that would override the auto-detect); set it in `config.local.json` to persist a machine-specific value |

## Test-to-module map

| test file | covers |
|-----------|--------|
| `test_keywords.py` | the regex core: boundaries, plurals, `p.e.` pins, overlap, pages, dedup; `match_stamp_cluster`: cluster found, designation/license alone, beyond-window, license-pattern variants, boilerplate non-matches, snippet shape, earliest-page/closest-pair determinism |
| `test_extract.py` | text-layer-vs-OCR, OCR replaces sparse text, multi-frame TIFF, errors, `_needs_ocr`, `aggregate_source`, tesseract wiring |
| `test_stamp.py` | delegation to `match_stamp_cluster`, cluster → `StampCluster`, no cluster → `None` |
| `test_verdict.py` | cluster → STAMP with designation/page/evidence, `None` → NO_STAMP, field carry-through |
| `test_config.py` | `load_config` read/parse, `load_config_with_local` overlay + merge, `validate_config` accept/reject matrix (incl. `jobs` int > 0 / absent / wrong-type) |
| `test_scan.py` | extension filter, sorting, dot-dir skip, nonexistent root |
| `test_report.py` | `verdict_to_row` schema (incl. `evidence`), `write_report` contract, `ReportWriter` incremental flush, `read_report` + `normalize_row` (missing `evidence` defaults to `""`) |
| `test_cli.py` | `_build_arg_parser` defaults + usage errors (exit 2), `_row_to_verdict` (round-trips `evidence`) |
| `test_cli_main.py` | full `main()` integration with fakes + fake clock, `evidence` column in the CSV, `--resume` (skip, complete report, missing file → exit 2, all-processed, empty resume file), `--jobs` resolution (flag > config > `min(CPU count, 4)` cap, `--jobs 0` → exit 2, jobs=1 vs jobs=2 identical rows) |

All tests run with no real PDFs, no Tesseract binary, no network. Run
`pytest tests/` from `findasbuilts/`.

## "Where to look" quick index

- **Why is a doc stamped?** → `stamp.py` (semantics) + `keywords.py`
  (regex rules).
- **Why did a page get OCRed?** → `extract.py::_needs_ocr`.
- **Why is a doc ERROR?** → `cli.py::main` (the `except` blocks).
- **What's in the CSV?** → `report.py::verdict_to_row`.
- **What config is valid?** → `config.py::validate_config`.
- **How do I add a stamp keyword?** → edit `keywords.stamp` in
  `config.json` (see README for the looser-terms caveat).
