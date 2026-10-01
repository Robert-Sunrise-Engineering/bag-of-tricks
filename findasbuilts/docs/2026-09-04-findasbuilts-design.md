# findasbuilts design — scan a directory for engineer-stamped as-builts

## Context

Sunrise Engineering triages a directory of as-built documents to find the
ones authoritative enough to update their GIS from — those carrying an
engineer's stamp. Today this is a manual eyeball job.

**Scope decision (user, 2026-09-04):** v1 detects the **engineer's stamp
only**. GIS-updatable-content matching is deliberately out of scope — the
user judges that themselves ("I can figure that part out myself"). So the
tool answers one question per document: *stamped, not stamped, or error?*

`findasbuilts/` is a greenfield directory in the `bag-of-tricks` monorepo.
The sibling `data-conflation/` project provides the conventions to follow
(entry-point shim, module layout, report contract, CLI/logging style, test
layout, docs shapes); its contracts were verified by exploration, not
assumed. There is **no existing PDF/OCR/text-extraction code anywhere in
the monorepo** — everything here is new.

**Confirmed decisions:** mixed PDFs (text layer first, OCR fallback per
page) · text-keyword stamp detection (no image/circle detection) · console
list + CSV report · **v1 scans PDFs only** (`file_extensions: [".pdf"]`;
the image path is dormant — see Extraction) · **stamped = a designation +
license-number cluster** (an engineer designation within 250 chars of a
license number on the same page; see Algorithm) · stamp designation list
configurable via git-tracked `config.json`.

**Scope change (user, 2026-09-04, same day):** the earlier "multi-page
TIFFs fully OCRed" decision was reversed — the user's document set is PDFs
only, and scanning loose images (PNG/JPG/TIFF) added OCR noise and runtime
for no benefit. The image path stays in the code (tested, dormant) so
re-adding extensions to `file_extensions` re-enables it with no code
change.

## Algorithm

### `match_keywords` — the correctness core (`findasbuilts/keywords.py`)

Each keyword is compiled once into a whole-word-ish regex:

```
(?<![A-Za-z0-9])<re.escape(keyword)>(?:s)?(?![A-Za-z0-9])
```

with `re.IGNORECASE`. The lookarounds give whole-word matching (`valve`
does not match inside `valvebox`), the optional `(?:s)?` handles plurals,
and `p.e.` matches at line end and before punctuation (`,`, `.`, `\n` all
fall outside the trailing lookahead).

**Overlap is intended and documented:** text "registered professional
engineer" matches both `registered professional engineer` and
`professional engineer` — `KeywordMatch.matched` is an *evidence
inventory*, not a best-match selection.

### `match_stamp_cluster` — the stamp rule (`findasbuilts/keywords.py`)

A document is stamped only when the evidence *clusters*: an engineer
designation (from the configurable `keywords.stamp` list) and a license
number appear within `max_distance` (250) characters on the same page. The
license number is matched by a built-in pattern, not a keyword:

```
(?<![A-Za-z0-9])(?:state\s+of\s+utah\s+(?:no\.?|number|#)
 | utah\s+(?:no\.?|number)
 | (?<![A-Za-z']\s)lic(?:ense|\.?)\s+(?:no\.?|number|#)
 | (?<![A-Za-z]\s)(?:no\.?|number))\s*[:#]?\s*\d{6,8}(?!\d)
```

(IGNORECASE) — matches `State of Utah No. 12338863`, `Utah No. 4777017-2202`,
`License #12338863`, `No. 12338863`. The license number must carry a
`Utah`/`State of Utah` prefix, a `license`/`lic.` prefix not preceded by a
word (rejects `Bidder's License`), or a bare `no.`/`number` not preceded by
a word (rejects `Project No`); the 6-8 digit floor and the whole-word
lookarounds reject water-right numbers (`APPLICATION/CLAIM NO.: A24354` —
letters), 4-digit contract numbers (`Work Release No. 2020`), water-system
numbers (`System #13060`), project numbers (`Project No: 220638`), file
numbers (`File #15254`), bidder's/operator's licenses, and bank accounts
(`#5110788`). The `#` form requires an explicit `State of Utah` prefix —
bare `Utah #12345` is bank-account style. This matters because a text-layer
line break can split "...State Bank of Southern\nUtah #5110788", defeating
the `(?<![A-Za-z0-9])` lookbehind (it sees only the newline, not the "n" of
"Southern") and letting the account number match near a signature's "P.E.".
Bare-Utah license numbers on the corpus are always typed `Utah No. 4777017-2202`.

`match_stamp_cluster` iterates pages in order, finds designation positions
(via the same whole-word-ish regex as `match_keywords`) and license
positions, and returns the closest designation↔license pair within
`max_distance` on the earliest such page, as a `StampCluster` with the
evidence snippet (~80 chars either side, newlines collapsed).

**Why 250, not 300:** on the live KCWCD corpus (376 docs, text layer), the
one real stamp ("Culinary Master Plan", *"Nathan Wallentine, P.E."* ~77
chars from *"No. 12338863"*) is found at `max_distance=250`; a
contract-number false positive ("Work Release No. 2020" near a signature
block's "P.E.") sits at ~296 chars and is excluded. The cluster rule
eliminates all 78 loose-phrase false positives (signature-block "P.E.",
boilerplate "professional engineer", form-instruction "license number")
with 0 new false positives and 0 missed real stamps.

### Verdict rules (`findasbuilts/verdict.py`)

- A `StampCluster` found → `STAMP` (designation, page, and evidence snippet
  recorded).
- No cluster → `NO_STAMP`.
- Extraction raised → `ERROR` (constructed by `cli.py`, not
  `evaluate_document`). ERROR rows are data, not run failure — a run where
  everything is `NO_STAMP` or some docs are `ERROR` still exits 0.

### Extraction (`findasbuilts/extract.py`)

- **PDF path**: PyMuPDF text layer per page; a page whose
  whitespace-stripped text is below `min_chars_for_text_layer` is rendered
  at `ocr_dpi` and OCRed. The pixmap is round-tripped through PNG +
  `PIL.Image.open(...).convert("RGB")` — this exact recipe — because
  `Image.frombytes` is a colorspace/alpha minefield (fitz pixmaps come back
  RGB/RGBA/CMYK) and some Tesseract builds mishandle alpha. OCR output
  **replaces** the sparse text-layer text (presumed noise). Encrypted PDFs
  and zero-page PDFs raise up front.
- **Image path** (non-PDF): every frame of `PIL.ImageSequence.Iterator` is
  OCRed — reading only frame 1 would silently miss stamps on sheets 2..N of
  a scan set. `page_count` = number of frames. **Dormant in v1:** the
  default `file_extensions` is `[".pdf"]`, so the scan never yields a
  non-PDF and this path is unreachable from the CLI. Kept (and tested) so
  adding extensions back re-enables it with no code change.
- `pytesseract.pytesseract.TesseractNotFoundError` is **not** caught in
  `extract.py` — it propagates to `cli.py`, which warns once per run and
  marks OCR-needing documents as `ERROR` rows with
  `error="OCR unavailable: Tesseract not found"`. Text-layer-only documents
  are unaffected; text-layer-only runs need no Tesseract binary at all.

## Worked examples (script-verified)

The regex cases below were verified with a standalone Python script
(reproduced verbatim) before/at implementation. Anyone doubting these
numbers can re-run the same script from these inputs.

```
OK  case-insensitive                    kw='license no'                 text='LICENSE NO 12345'                  -> True
OK  word boundary: valve not in valvebox kw='valve'                     text='valvebox assembly'                 -> False
OK  word boundary: valve standalone      kw='valve'                     text='the valve is closed'               -> True
OK  plural: engineer seals               kw='engineer seal'             text='engineer seals'                    -> True
OK  p.e. at end of string                kw='p.e.'                      text='P.E.'                              -> True
OK  p.e. before newline                  kw='p.e.'                      text='P.E.\n'                            -> True
OK  p.e. before comma                    kw='p.e.'                      text='P.E.,'                             -> True
OK  p.e. before period                   kw='p.e.'                      text='P.E..'                             -> True
OK  p.e. before space                    kw='p.e.'                      text='P.E. 12345'                        -> True
OK  p.e. followed by alphanumeric        kw='p.e.'                      text='P.E.123'                          -> False
OK  multi-word                           kw='certificate of authorization' text='certificate of authorization'  -> True
OK  overlap: professional engineer in registered kw='professional engineer' text='registered professional engineer' -> True
OK  overlap: registered professional engineer kw='registered professional engineer' text='registered professional engineer' -> True
```

The `p.e.` cases pin the exact strings `"P.E.\n"` and `"P.E.,"` in
`tests/test_keywords.py` — the trailing lookahead must not reject
punctuation or end-of-string.

The `match_stamp_cluster` cases (designation + license-number proximity,
`max_distance=250`) were verified the same way:

```
OK  cluster found: P.E. near No. 12338863   text='Nathan Wallentine, P.E. ... No. 12338863'  -> StampCluster(d='p.e.', lic='No. 12338863', dist<250)
OK  designation alone: signature block      text='Jesse Ralphs, P.E.'                        -> None
OK  license alone: form instruction          text='License No. 123456'                       -> None
OK  beyond window (300 chars)                text='P.E. ' + ' '*300 + ' No. 123456'          -> None
OK  license variants                         text='P.E. State of Utah No. 123456' / 'License #123456' / 'lic. no. 123456' -> cluster
OK  water-right number (letters)             text='P.E. APPLICATION/CLAIM NO.: A24354'       -> None
OK  4-digit contract number                  text='Jesse Ralphs, P.E. Work Release No. 2020' -> None
OK  water-system number                      text='Scott D. Hacking, P.E. System #13060'     -> None
OK  project number                           text='John Jacobsen, P.E. Project No: 220638'  -> None
OK  file number                              text='J. Paul Wright, P.E. File #15254'         -> None
OK  bidder's license                         text='Steve Johansen Bidder's License No.: 233855-5501' -> None
OK  operator's license                       text='P.E. License Number: 12891'               -> None
OK  bank account number                      text='Ken Hoffman, P.E. checking acct #5110788' -> None
OK  bank acct w/ Utah context                text='Ken Hoffman, P.E. checking acct State Bank of Southern Utah #5110788' -> None
OK  bank acct, linebreak-split               text='Ken Hoffman, P.E. checking acct State Bank of Southern\nUtah #5110788' -> None
```

The `\d{6,8}` floor (not `\d{3,}`) is deliberate: a 4-digit contract number
like "No. 2020" near a signature block's "P.E." would otherwise
false-positive, and 5-digit water-system numbers (`System #13060`) and
operator's licenses (`License Number: 12891`) would too. The context
requirement (a `Utah`/`license`/bare-`no.` prefix) rejects project numbers
(`Project No: 220638`), file numbers (`File #15254`), bidder's licenses, and
bank accounts. On the full KCWCD corpus (11,178 docs), the tightened pattern
eliminates all 13 false positives found by the 5+ digit pattern while keeping
all 70 genuine title-block matches and the real seal-text stamps.

The `#`-form gate (bare `Utah #` rejected) closes a linebreak hole found on
the full-corpus re-run: two disbursement-request forms read "...State Bank of
Southern\nUtah #5110788" — the text layer split the phrase, so the
`(?<![A-Za-z0-9])` lookbehind saw only the newline (not the "n" of
"Southern") and the account number matched near the signature's "P.E.".
Requiring `State of Utah #` (not bare `Utah #`) eliminates both while keeping
every genuine `State of Utah #5571333`-style seal. A surveyor's certificate
("Professional Engineers and Professional Land Surveyors Licensing Act" +
the surveyor's "License No. 5561917") still scores `STAMP` — accepted as a
known scope nuance (a signed survey plat is authoritative for parcel
geometry; see Scope guardrails).

## Testing

All unit tests run with no real PDFs, no Tesseract binary, no network. No
`conftest.py`, no markers; run pytest from `findasbuilts/`; imports
`from findasbuilts.cli import ...`. Docstring header on every test module
saying what's covered and what's covered elsewhere.

- `test_keywords.py` — the core: case-insensitivity, word boundaries
  (`valvebox` no-match shape), plurals, `p.e.` at end-of-string and before
  punctuation, multi-word keywords, the overlap pin, page tracking, dedup,
  empty/no-match; plus `match_stamp_cluster`: cluster found, designation
  alone / license alone / beyond-window → None, license-pattern variants
  (`No. 123456`, `License No. 123456`, `State of Utah No. 123456`,
  `License #123456`, `lic. no. 123456`), boilerplate non-matches ("proof
  professional's name and license number", "APPLICATION/CLAIM NO.: A24354",
  "Work Release No. 2020"), the full-corpus false-positive classes
  (water-system `System #13060`, project `Project No: 220638`, file
  `File #15254`, bidder's `Bidder's License No.: 233855-5501`, operator's
  `License Number: 12891`, bank `#5110788`), snippet shape,
  earliest-page/closest-pair determinism.
- `test_extract.py` — monkeypatched `extract.fitz`/`extract.pytesseract`/
  `extract.PIL` fakes: text-layer-vs-OCR decision, OCR output replacing
  sparse text, multi-frame image iteration, encrypted/zero-page PDF errors,
  `_needs_ocr` pure cases, `aggregate_source`, `tesseract_cmd`/`--psm`
  wiring.
- `test_stamp.py` — delegation to `match_stamp_cluster`: cluster →
  `StampCluster`, no cluster → `None`.
- `test_verdict.py` — cluster → `STAMP` with designation/page/evidence;
  `None` → `NO_STAMP`; fields carried through.
- `test_config.py` — `validate_config` accept/reject matrix (no save_config
  tests — cut with the function).
- `test_scan.py` — extension filter, case-insensitive suffixes, sorted,
  skips `.`-dirs; nonexistent root yields nothing.
- `test_report.py` — union-of-keys/empty-rows/parent-dir; `verdict_to_row`
  column set (incl. `evidence`); `normalize_row` defaults missing `evidence`
  to `""` (old resume files stay readable).
- `test_cli.py` — pure helpers only: `_build_arg_parser` defaults;
  `--min-chars 0`/`-5` → exit 2; missing-DIRECTORY → exit 2;
  `_row_to_verdict` round-trips `evidence`.
- `test_cli_main.py` — full `main()` integration with fakes + fake clock:
  happy path (STAMP fixture is a cluster-forming string like
  `"P.E. No. 123456"`; `evidence` column asserted), one failing doc → ERROR
  row without abort, Tesseract-missing → warning once + `OCR unavailable`
  ERROR rows + text-layer docs unaffected, empty dir → empty CSV + exit 0,
  `--min-chars` reaches extraction, nonexistent directory → exit 2.

## Dependency

`requirements.txt` (`>=` pin style, sibling parity): `PyMuPDF>=1.24.0`,
`pytesseract>=0.3.10`, `Pillow>=10.0.0`, `pytest>=8.0.0`. **System
dependency:** Tesseract binary (Windows: UB Mannheim installer or
`choco install tesseract`; default
`C:\Program Files\Tesseract-OCR\tesseract.exe`; see the local-config note
in the README if not on PATH). Text-layer-only paths need no Tesseract.

## Scope guardrails

1. **GIS-content detection is deliberately out of v1** — user decision
   2026-09-04. The keyword matcher is general enough to grow a second list
   later (e.g. a `gis_content` list alongside `keywords.stamp`).
2. **v1 stamps only on a designation + license-number cluster** — a loose
   phrase alone ("Jesse Ralphs, P.E." in a signature block, boilerplate
   "professional engineer", form-instruction "license number") is `NO_STAMP`.
   The cluster rule replaced the earlier single-keyword-hit rule after the
   live KCWCD run showed ~99% of `STAMP` verdicts were false positives (79
   STAMP, 1 real). `max_distance` is a module constant
   (`DEFAULT_MAX_CLUSTER_DISTANCE = 250`), not yet configurable.
3. **No config writer in v1**; if one is added, port the sibling's atomic
   `save_config` byte contract (mkstemp + `os.replace`, indent 2, CRLF, no
   trailing newline).
4. **Bare `seal`/`stamp`/`state of`/`state engineer` stay out of the
   defaults** — "seal coat" and "joint seal" are pavement/pipe-spec
   vocabulary, "STATE OF UTAH" appears in standard title blocks of unstamped
   plan sets, and "State Engineer" is the name of the Utah Division of Water
   Rights' head — a phrase that saturates water-rights paperwork regardless
   of stamp status (verified 2026-09-04: on a KCWCD corpus it alone drove
   ~50% of `STAMP` verdicts, all false positives). A false `STAMP` is the
   expensive error for this tool (unauthoritative docs greenlit for GIS).
   The README documents these as optional looser terms users can add.
5. **A surveyor-signed plat can score `STAMP` — accepted** (user decision
   2026-09-07). The statutory title "Professional Engineers and Professional
   Land Surveyors Licensing Act" contains the designation phrase, and the
   surveyor's own "License No. 5561917" is format-identical to an engineer's
   license; no regex can reliably separate them. Accepted because a signed
   survey plat is authoritative for parcel geometry — not a harmful
   greenlight for the GIS-triage purpose. Documented in the README.
6. **Resume/restart is implemented** — `--resume <report.csv>` reads a prior
   run's report, skips documents whose `file_path` is already present (no
   re-extract/re-OCR), and writes a NEW timestamped report containing the
   carried-forward rows plus the newly processed rows — a complete superset,
   so one file is the full deliverable. A missing/unreadable resume file is
   a usage error (exit 2). For large corpora (an 11k-doc KCWCD run,
   2026-09-04) this turns a mid-run crash into a cheap continuation instead
   of a full re-scan.

## Verification (once implemented)

- Full `pytest tests/` run passes with no Tesseract binary needed (OCR
  mocked).
- Sample-dir end-to-end run: `stamped_text.pdf` → `STAMP`/`text_layer`;
  `stamped_scanned.pdf` (image-only) → `STAMP`/`ocr`; `unstamped.pdf` →
  `NO_STAMP`. (The `multipage.tif` sample is no longer scanned — v1
  `file_extensions` is `[".pdf"]`; the image path is verified by the mocked
  unit tests in `test_extract.py`.)
- **KCWCD corpus re-run** (376 docs, text layer): **1 STAMP** (Culinary
  Master Plan) instead of 79, with the `evidence` column showing the seal
  snippet — all 78 loose-phrase false positives eliminated, 0 new false
  positives, 0 missed real stamps.
- **Full KCWCD corpus re-run** (11,178 docs, 2026-09-07): **109 STAMP** —
  106 genuine title-block seals + 2 bank-account false positives (fixed by
  the `#`-form gate) + 1 accepted surveyor's certificate. The 2 bank docs
  re-verified clean against the real PDFs after the gate; all 180 unit tests
  pass. (Final clean re-run with the gate in place: **107 STAMP** expected.)
- **`--resume` from an old report** (predates the `evidence` column) still
  works — carried rows get `evidence=""`.
- Edge runs: empty directory → empty CSV + "0 documents" + exit 0;
  nonexistent directory → exit 2; `--min-chars 0` → exit 2.
- Resume edge runs: `--resume <partial.csv>` skips already-processed docs
  (no re-extract) and writes a complete report; all docs already present →
  warn + carried rows + exit 0; empty resume file → processes everything;
  missing resume file → exit 2; empty directory with `--resume` → still
  exit 0 (empty-dir check runs first).
- `git status`: `reports/` ignored (only `.gitkeep` tracked), `config.json`
  tracked.

## Parallelism (implemented 2026-09-09)

The design above predicted *"~30 h single-threaded, ~4 h with `--jobs 8`"* —
but v1 shipped with no `--jobs` flag, and a full 8,048-doc corpus run took
~16.3h, ~85% of it OCR (measured: ~1–1.4s per OCR page; network+parse only
~10–15%). Each document is independent (no cross-document state), so the work
is embarrassingly parallel.

**Implementation:** `cli.main()` now runs the per-document pipeline through a
`concurrent.futures.ThreadPoolExecutor`. **Threads, not processes** — the
heavy work (Tesseract subprocess via pytesseract, PyMuPDF `get_pixmap`
render) releases the GIL, and threads avoid Windows pickling issues. The
per-document work moved into a module-level `_process_document(path,
ocr_settings, stamp_keywords)` worker (extract → detect → evaluate), which
converts every exception to an ERROR verdict so one bad document never aborts
the run. The main thread iterates `as_completed(futures)`, warns **once** per
run when it sees the `TesseractNotFoundError` marker string (moved out of the
worker into a shared `OCR_UNAVAILABLE_ERROR` constant — no race, the check
runs sequentially in the main thread), and writes rows. `ReportWriter` stays
single-threaded (main thread only), preserving the incremental flush that
makes interrupted runs resumable. On `BaseException` (Ctrl+C or an unexpected
main-thread error like a full disk on `report.append`):
`executor.shutdown(wait=False, cancel_futures=True)` then re-raise, so the
partial report is preserved; in-flight OCR threads (up to `jobs`) finish in
the background — accepted trade-off. The normal path calls
`executor.shutdown(wait=True)` — required, or worker threads keep the
interpreter alive on exit.

**`jobs` resolution:** `--jobs N` flag > config `jobs` key > conservative
auto-detect `min(os.cpu_count() or 1, 4)`. The cap is a user decision
(2026-09-09): this machine has 16 logical processors, and 16 concurrent
readers would hammer the network drive (SMB rate-limiting). The key is
optional in config — it is deliberately **not** in `config.json` (that would
override the auto-detect default for everyone); a machine sets it in
`config.local.json` or passes `--jobs N` per run. `validate_config` accepts
`jobs` only as an int > 0 when present (bool guard).

**Known trade-offs:** CSV row order is completion order (non-deterministic
under threads) — irrelevant to `--resume`, which reads rows into a set of
`file_path`s. The `Processing <name> ...` line no longer carries `i/n` (the
worker doesn't know its index); the completion line carries it. The
pytesseract global `tesseract_cmd` write is a benign write-write race — every
worker shares the same `ocr_settings` and writes the same value.
