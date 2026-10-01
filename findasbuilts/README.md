# findasbuilts

Scan a directory of as-built documents and find the ones carrying an
engineer's stamp — the mark a licensed professional engineer applies to
certify that work was prepared under their responsible charge.

v1 answers one question per document: **stamped, not stamped, or error?**
It detects the stamp by matching text keywords only (no image/circle
detection). GIS-updatable-content matching is deliberately out of scope —
you judge that part yourself.

## Requirements

- Python 3.10+
- Tesseract OCR binary — **only needed for scanned/image documents.**
  Text-layer-only PDFs work without it.
  - Windows: [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki)
    or `choco install tesseract`. Default path:
    `C:\Program Files\Tesseract-OCR\tesseract.exe`.
  - If Tesseract is not on PATH, point the tool at it via
    `ocr.tesseract_cmd` in a local config (see Configuration).

## Installation

```powershell
cd findasbuilts
python -m pip install -r requirements.txt
```

## Usage

```powershell
python findasbuilts_main.py <directory> [--config config.json] [--report-dir reports] [--min-chars N] [--jobs N] [--resume report.csv] [--verbose]
```

> **PowerShell gotcha:** always invoke with the `python` prefix. A bare
> `findasbuilts_main.py` launches in a separate window.

Example:

```powershell
python findasbuilts_main.py C:\asbuilts\2026
```

Output is a console listing plus a timestamped CSV report:

```
Scanned 42 document(s) in C:\asbuilts\2026
Stamped (7):
  - C:\asbuilts\2026\waterline\wm-104.pdf
Not stamped (33):
  - C:\asbuilts\2026\sewer\sw-201.pdf
Errors (2):
  - C:\asbuilts\2026\scan-88.tif: OCR unavailable: Tesseract not found
Report: reports\findasbuilts_20260904_101500.csv
```

During the run, progress lines announce the scan and each document as it's
processed — `Scanning <dir> for documents ...`, then `Found N document(s);
report -> <path>` once the scan completes, then `Processing <name> ...`
before each extraction (up to `--jobs` at once, one per worker thread) and
`i/n <name> -> <verdict>` as each finishes — so a large tree or a slow first
OCR never looks like a hang. These progress lines go to stderr; the summary
above goes to stdout.

The CSV report is written **incrementally** — each document's row is
flushed to disk as it's processed, and the per-document console line shows
the error message for `ERROR` rows. So a run you stop early (Ctrl+C) still
leaves a report of everything processed so far, and you can see *why* a
document failed without waiting for the run to finish. Each row records the
verdict, the matched designation and page, and an `evidence` column — the
text snippet around the stamp cluster, for human verification of a `STAMP`
verdict.

### Resuming an interrupted run

If a run is interrupted, the partial report it left behind is a valid
`--resume` input:

```powershell
python findasbuilts_main.py C:\asbuilts\2026 --resume reports\findasbuilts_20260904_101500.csv
```

Documents whose `file_path` is already in the resume report are **skipped**
(no re-extract, no re-OCR); the rest are processed. The run writes a **new**
timestamped report containing *all* documents — the carried-forward rows plus
the newly processed rows — so one file is the full deliverable. The console
shows the complete picture with a `Resumed from <path>: skipped M
document(s)` line. A missing or unreadable resume file is a usage error
(exit 2); an empty resume file simply processes everything.

### Options

| flag | default | meaning |
|------|---------|---------|
| `directory` | — | directory to scan (required) |
| `--config` | `config.json` | path to the base config JSON (a sibling `<name>.local.json` is auto-merged) |
| `--report-dir` | `reports` | where to write report CSVs |
| `--min-chars` | config value | override `ocr.min_chars_for_text_layer` for this run (must be ≥ 1) |
| `--jobs` | `min(CPU count, 4)` | parallel worker threads; each runs its own Tesseract subprocess. The default is capped at 4 so a many-core machine doesn't open too many concurrent readers on a network share — raise it for local disks |
| `--resume` | none | resume from a prior report CSV: skip documents already present and write a new complete report |
| `--verbose` | off | DEBUG logging |

### Exit codes

| code | meaning |
|------|---------|
| 0 | run completed (even if some docs are `NO_STAMP` or `ERROR`) |
| 1 | config load/validation failure |
| 2 | usage error (missing/unreadable directory, `--min-chars < 1`, missing/unreadable resume file) |

## Configuration

`config.json` is git-tracked and holds the whole tool's behavior. A
`config.local.json` next to it is auto-merged over it (see below). See
`docs/REFERENCE.md` for the full schema reference.

### `ocr` settings

| key | default | meaning |
|-----|---------|---------|
| `min_chars_for_text_layer` | `20` | a PDF page with fewer whitespace-stripped chars is OCRed instead of trusted as a text layer |
| `ocr_dpi` | `300` | render DPI for the OCR fallback |
| `tesseract_lang` | `"eng"` | Tesseract language pack |
| `tesseract_psm` | `null` | passed as `--psm <n>`; `11` (sparse text) materially improves as-built OCR |
| `tesseract_cmd` | `null` | machine-specific Tesseract binary path |

### `keywords.stamp` — the engineer-designation list

A document is `STAMP` only when the evidence **clusters**: an engineer
designation from this list appears within 250 characters of a license number
(`State of Utah No. 12338863`, `Utah No. 4777017-2202`, `License #12338863`,
`No. 12338863`) on the same page. A loose phrase alone — "Jesse Ralphs, P.E."
in a signature block, or "proof professional's name and license number" in
form instructions — is not a stamp. The defaults:

```
professional engineer, registered professional engineer, licensed professional engineer,
p.e., pe seal, engineer's seal, engineer seal, registered engineer,
licensed engineer, certificate of authorization
```

The license number is matched by a built-in pattern, not a keyword — so
`license no`/`license number` are *not* in the list. The pattern requires a
`Utah`/`State of Utah` prefix, a `license`/`lic.` prefix not preceded by a
word (rejects "Bidder's License"), or a bare `no.`/`number` not preceded by
a word (rejects "Project No"), followed by 6-8 digits. The `#` form requires
an explicit `State of Utah` prefix — bare `Utah #12345` is bank-account
style (a text-layer line break in "...State Bank of Southern\nUtah #5110788"
would otherwise let the account number match near a signature's "P.E.").
That context requirement and digit floor keep water-system numbers
(`System #13060`), project numbers (`Project No: 220638`), file numbers
(`File #15254`), bidder's/operator's licenses, and bank accounts
(`#5110788`) from false-positive when they sit near a "P.E.". The 250-char
window (`max_distance`) is what separates a real seal from a signature
block's "P.E." with no license number nearby.

**Known scope nuance:** a signed survey plat can score `STAMP` — the
statutory title "Professional Engineers and Professional Land Surveyors
Licensing Act" contains the designation phrase, and the surveyor's own
"License No. 5561917" is format-identical to an engineer's. A regex cannot
reliably tell the two apart. This is accepted: a surveyor-signed plat is
authoritative for parcel geometry, so it is not a harmful greenlight.

**Optional looser terms** — deliberately *not* in the defaults, because a
false `STAMP` is the expensive error for this tool (unauthoritative docs
greenlit for GIS):

- `seal` / `stamp` — "seal coat" and "joint seal" are pavement/pipe-spec
  vocabulary.
- `state of` — "STATE OF UTAH" appears in standard title blocks of
  unstamped plan sets.
- `state engineer` — the Utah State Engineer is the head of the Division of
  Water Rights, so the phrase appears throughout water-rights paperwork
  (applications, segregation requests, DWRi correspondence) whether or not
  the document carries a stamp. On a water-district corpus this keyword
  alone produced ~50% false-positive `STAMP` verdicts.

Add them to `keywords.stamp` only if your document set warrants it.

### Local (machine-specific) config

`config.local.json` is git-ignored — use it for machine-specific overrides
like a Tesseract path. It is **auto-merged** over the base config when it
sits next to it, so you don't need a `--config` flag:

```powershell
Copy-Item config.json config.local.json
# edit config.local.json: set ocr.tesseract_cmd to your Tesseract path
python findasbuilts_main.py C:\asbuilts\2026
```

The merge is deep: a partial local file (say, only `ocr.tesseract_cmd`)
leaves the rest of the base config intact; lists (e.g. `keywords.stamp`)
are replaced wholesale, not concatenated.

A machine-specific `jobs` value goes here too — e.g. `"jobs": 8` if this
machine's corpus lives on a local disk. It is **not** in the base
`config.json` (that would override the auto-detect default for everyone);
the `--jobs` flag beats it for a one-off run.

## How it works

1. **Scan** the directory for PDFs (the v1 `file_extensions` list is
   `[".pdf"]`), skipping dot-directories.
2. **Extract** per-page text: the PyMuPDF text layer, falling back to
   Tesseract OCR per page when a page is too sparse.
3. **Detect** a stamp cluster: an engineer designation within 250 characters
   of a license number on the same page.
4. **Evaluate** each document to `STAMP` / `NO_STAMP` / `ERROR`.
5. **Report** a console listing plus a timestamped CSV, written
   incrementally so an interrupted run still leaves a report.

If Tesseract is missing, the tool warns once and marks OCR-needing
documents as `ERROR` rows — text-layer-only documents are unaffected.

## Documentation

- `docs/REFERENCE.md` — module-by-module reference, schemas, invariants,
  config schema, test map.
- `docs/2026-09-04-findasbuilts-design.md` — design decisions, the regex
  algorithm, script-verified worked examples, scope guardrails.

## Development

```powershell
cd findasbuilts
python -m pytest tests/
```

All tests run with no real PDFs, no Tesseract binary, and no network.
