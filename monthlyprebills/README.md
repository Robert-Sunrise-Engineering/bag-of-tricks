# Monthly Pre-Bill Review

A self-contained PowerShell 7 tool that reads the monthly Deltek/Vantagepoint
"Pre-billing summary" PDFs and answers two questions in minutes instead of hours:

1. Does a bill need to be issued this month?
2. Does additional revenue need to be recognized?

It extracts each PDF with `pdftotext`, applies deterministic billing rules per task line,
and writes an exception-focused console summary, CSV files, and a self-contained HTML
report. It never edits the PDFs and never calls an AI/LLM service.

## Requirements

- Windows PowerShell 7 (`pwsh`) — the script is `#Requires -Version 7.0`.
- `pdftotext` from Poppler or Xpdf. On this machine it is found automatically at
  `%ProgramFiles%\Git\ucrt64\bin\pdftotext.exe`; otherwise put it on `PATH` or set
  `pdfToTextPath` in `config.json` / `config.local.json`.
- Pester 3.4 or newer, only for the test suite. Pester 3.4-compatible syntax is used.

## Quick start

```powershell
cd C:\Users\user\projects\bag-of-tricks\monthlyprebills
.\Get-PreBills.ps1
```

By default the tool reads `.\newprebills\*.pdf` and writes a timestamped folder under
`.\reports\`. Open the HTML directly or ask the script to do it:

```powershell
.\Get-PreBills.ps1 -Open
```

Other common invocations:

```powershell
# Only amount-based decisions above $200; structural problems still surface.
.\Get-PreBills.ps1 -Threshold 200

# Include NO ACTION projects in the CSVs and HTML (the console always lists everyone).
.\Get-PreBills.ps1 -IncludeClean

# Use the project objects in a pipeline.
.\Get-PreBills.ps1 -PassThru | Where-Object Action -eq 'BILL NOW'

# CI-style gate: exit code 2 when a bill is due.
.\Get-PreBills.ps1 -FailOnBillNow
```

## Decision rules

Each task line is classified by its fee type:

| Fee type | Candidate bill | Review flags |
|---|---|---|
| Progress | `max(0, Earned Revenue - Previously Invoiced)` | `JTD Spent > Earned Revenue` (under-recognition, reported at project level) |
| Time and expense | `max(0, JTD Spent at Billing - Previously Invoiced)` | `JTD Spent > Earned Revenue` (under-recognition; project-level total plus a line-level unbilled-cost flag) |

Over-billed amounts (`Previously Invoiced > Earned Revenue`) are **not findings** in this
process and do not affect the action. They remain in the `OverBilled` / `OverBilledTotal`
CSV columns for projects included in the report; use `-IncludeClean` to include `NO ACTION`
projects. Only under-billing and under-recognition matter.

A line whose **Previously Invoiced % is at 100%** is fully billed: it produces no candidate
bill and no under-recognition flag, even if rounding makes Earned Revenue or JTD Spent
exceed Previously Invoiced.

Project verdict is the highest severity:

- **BILL NOW** — any line's candidate bill exceeds the threshold.
- **REVIEW** — any review flag fired (under-recognition / JTD above earned, totals mismatch,
  duplicate document, already-queued current invoice).
- **NO ACTION** — clean.
- **ERROR** — the document could not be parsed.

The `-Threshold` parameter (default `0.01`) gates amount-based decisions only. Structural
problems — totals mismatch, unparseable rows, missing header fields, duplicate
schedule+date, or `Current Billing Invoice > 0` — always surface.

Over-billing is not a finding in this process: the over-billed columns remain in the CSV
data for reference, but they neither raise a flag nor change the action. A totals mismatch
forces **REVIEW** (never **BILL NOW**) because the parsed figures cannot be trusted. A
nonzero `Current Billing Invoice` surfaces as a structural flag; the normal action hierarchy
still applies, so a candidate bill can make the project **BILL NOW**. The console and HTML
summaries show **Bill Now** and **Unbilled** (JTD spent above earned revenue); the project Notes
state `JTD spent exceeds earned revenue by $X` when any line exceeds the threshold, for
Progress and T&E alike. T&E lines also carry a line-level `Unbilled cost` flag. The totals row
and the HTML “To bill now” tile sum the Bill Now column across the report.

### Correctness principle

Every reported figure is read from the document as printed. Earned Revenue is **never**
recomputed from its percentage. The document's own rounding (for example 66.67% of $1,000 =
$666.70) is authoritative; recomputing would create false totals mismatches.

## Output

Each run creates `reports\<yyyy-MM-dd-HHmm>\`:

| File | Contents |
|---|---|
| `summary.csv` | One row per project, exception-focused by default |
| `detail.csv` | One row per phase/task line |
| `report.html` | Self-contained one-page report (no external CSS/JS) |
| `extracted\<pdf-name>.txt` | Raw `pdftotext -table` output for audit/debugging |

CSVs are UTF-8 with a BOM and use invariant-culture numbers without thousands separators,
so Excel reads them as numbers. Without `-IncludeClean`, `NO ACTION` projects are omitted
from the CSVs and HTML; the console summary always lists every project.

The console output is ordered `BILL NOW → REVIEW → NO ACTION → ERROR`, followed by a totals
row and line detail for each BILL NOW / REVIEW project.

## Configuration

`config.json` holds defaults; `config.local.json` holds machine overrides and is
git-ignored. Command-line parameters win over both.

```json
{
  "inputPath": "newprebills",
  "outputPath": "reports",
  "threshold": 0.01,
  "pdfToTextPath": ""
}
```

Relative paths in `config.json` / `config.local.json` are resolved against the script
directory. Relative command-line paths are resolved against the current directory.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success (including partial parse failures, which are reported as ERROR rows) |
| `1` | Fatal: no PDFs, `pdftotext` not found, input folder missing, or every document failed to parse |
| `2` | `-FailOnBillNow` was supplied and at least one project is BILL NOW |

## Troubleshooting

**`pdftotext.exe was not found`** — Install Poppler/Xpdf, or set `pdfToTextPath` in
`config.local.json` to the full executable path.

**A project is `ERROR` with "appears to be image-only"** — The PDF has no extractable text.
Run OCR first, or confirm the file is actually a pre-billing summary. `pdftotext` can exit 0
on a scanned PDF, so this check is deliberate.

**A project is `ERROR` with "template drift"** — The PDF layout changed and a dotted task
row did not match the expected columns. The message records the file and line; the parser
never guesses at a partial row.

**A project is `REVIEW` with "Totals mismatch"** — The summed line amounts differ from the
document's own total row by more than $0.01. Investigate before billing; the figures are
held at REVIEW on purpose.

**Two documents are flagged "Duplicate billing schedule/date"** — Two PDFs share the same
`Billing Schedule Id` and `To Date`. Both rows are kept so the count is visible.

**Tests** — `Invoke-Pester -Path .\tests\PreBills.Tests.ps1` (Pester 3.4-compatible). The
committed fixtures under `tests\fixtures` are wholly invented documents (fake projects,
descriptions, contract numbers, and amounts); they are safe to commit and do not depend on
the monthly PDFs. Do not copy real descriptions or figures into them.

## Data handling

The monthly PDFs are real client financial documents. `.gitignore` excludes:

- `newprebills/` — the input PDFs;
- `reports/` — generated reports and extracted text;
- `config.local.json` — machine-specific overrides;
- `PLAN.md` and `docs/2026-11-10-prebills-design.md` — local planning/validation records
  that contain client-specific details.

The test fixtures and expected values committed to this repository are synthetic. Do not
copy real monthly documents, extracted text, reports, or client-specific validation details
into tracked files. To verify a real batch, run the tool locally against the PDFs; the
output stays under the ignored `reports/` folder.
