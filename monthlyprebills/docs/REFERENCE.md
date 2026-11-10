# PreBills Module Reference

Module: `PreBills.psm1` (PowerShell 7). All functions are exported.

## Data shapes

### Parsed header

`[pscustomobject]` with `BillingScheduleId`, `ContractNumber`, `ContractName`,
`LegalEntity`, `FromDate`, `ToDate`. `FromDate` may be blank; it is not required.

### Parsed line

`[pscustomobject]` with:

| Property | Type | Notes |
|---|---|---|
| `TaskCode` | string | Dotted phase/task code, e.g. `PROJ.001.002.003` |
| `Description` | string | Continuation lines joined; whitespace collapsed |
| `FeeType` | string | `Progress` or `Time and expense` (normalized) |
| `ContractValue` | decimal | As reported |
| `PreviouslyInvoiced` | decimal | As reported |
| `PreviouslyInvoicedPct` | decimal | Percent value, e.g. `100.00` |
| `JtdSpentAtBilling` | decimal | As reported |
| `EarnedRevenuePct` | decimal | Percent value |
| `EarnedRevenue` | decimal | As reported; never recomputed from the percent |
| `PctToComplete` | decimal | Percent value |
| `CurrentBillingInvoice` | decimal | As reported |
| `SourceLineNumber` | int | 1-based line in the extracted text |

### Parsed document

`[pscustomobject]` with `Header`, `Lines` (array), `Totals` (object or `$null`),
`ParseErrors` (string array), `SourceFile`.

### Measured line

Parsed line fields plus `Action` (`BILL NOW` / `REVIEW` / `NO ACTION`), `BillNow`,
`OverBilled`, `JtdVsEarned`, and `Flags` (string array). A line at 100% previously invoiced
has `BillNow = 0` and `JtdVsEarned = 0`.

### Measured project

| Property | Meaning |
|---|---|
| `Action` | `BILL NOW`, `REVIEW`, `NO ACTION`, or `ERROR` |
| Header fields | Copied from the parsed header |
| `ContractValue`, `PreviouslyInvoiced`, `JtdSpentAtBilling`, `EarnedRevenue`, `CurrentBillingInvoice` | Document-reported totals when available, else summed lines |
| `BillNowTotal` | Sum of candidate bills across lines |
| `UnbilledProgress` | `BillNow` sum for Progress lines (candidate-bill split) |
| `UnbilledTandM` | `BillNow` sum for Time-and-expense lines (candidate-bill split) |
| `OverBilledTotal` | Informational data: sum of `max(0, PreviouslyInvoiced - EarnedRevenue)`; does not drive flags or actions |
| `JtdVsEarnedTotal` | Unbilled / under-recognition amount: sum of `max(0, JtdSpentAtBilling - EarnedRevenue)` |
| `Flags` | Deduplicated project and line flags, plus parse errors |
| `SourceFile` | PDF file name |
| `Lines` | Measured line array |
| `Totals` | Parsed total row or `$null` |
| `ParseErrors` | String array; non-empty means `ERROR` |
| `RawText` | Extracted text used for the audit copy |

## Functions

### `Resolve-PdfToTextPath [-Override <path>]`

Returns the full path to `pdftotext.exe`. Search order: `-Override`, `Get-Command
pdftotext`, `%ProgramFiles%\Git\ucrt64\bin\pdftotext.exe`,
`%ProgramFiles%\Git\usr\bin\pdftotext.exe`. Throws an actionable error if none exists.

### `Get-PreBillRawText -Path <pdf> -PdfToTextPath <exe>`

Runs `pdftotext -table -enc UTF-8 <pdf> -` and returns stdout. Throws when the exit code is
non-zero. Also throws when the output does not contain `Pre-billing summary`, because a
scanned/image-only PDF can exit 0 with no usable text.

### `ConvertFrom-Money [-Value <string>]`

Invariant-culture decimal parser. Blank → `0`; `(123.45)` → `-123.45`; commas removed;
non-numeric text throws.

### `ConvertFrom-Percent [-Value <string>]`

Parses `100.00%` → `100.00`. Blank → `0`.

### `ConvertFrom-PreBillText -Text <string> [-SourceFile <string>]`

Parses extracted text into a parsed document. Behavior:

- Normalizes CRLF/CR and form feeds, strips a leading BOM.
- Truncates at `Transactions summary`; only the `Progress summary` table is used.
- Requires the `Pre-billing summary` marker; otherwise records an image-only/not-a-summary
  `ParseError`.
- Reads the two-column header. `Contract name` stops at `\s{2,}To date`; an indented,
  colon-free, pipe-free continuation line is appended (this is how wrapped names are
  recovered).
- Data rows require a dotted task code, a `|`, the fee-type token, and all five money and
  three percent tokens. Group rows such as `PROJ_001 | ...` therefore cannot be
  counted as data rows. `Time\s+and\s+expense` accepts the one- to three-space variants
  seen in real files.
- Description continuations are gathered structurally: blank spacer lines are skipped and
  non-empty, colon-free, pipe-free lines are joined until the next task row, total row,
  section header, or repeated `Pre-billing summary` page header. A word ending in `-` is
  joined without a space (`Multi-` + `Tenant Widget Support` → `Multi-Tenant Widget
  Support`); a spaced dash (`Apr 1 -` + `Sep 30`) keeps its space. More than five
  continuation lines records a `ParseError` rather than truncating silently.
- Recognizes the five-column amount-only total row.
- A dotted task row that does not match the full pattern is recorded as a
  `ParseError` (template drift); the parser never guesses.
- Missing required header fields (`Billing schedule Id`, `Contract number`, `Contract
  name`, `Legal entity`, `To date`), a missing `Progress summary`, zero task rows, or a
  missing total row are `ParseError`s.

### `Measure-PreBill -Parsed <object> [-Threshold <decimal>]`

Applies the rules and returns a measured project.

- Progress candidate bill: `max(0, Earned - PreviouslyInvoiced)`.
- Time-and-expense candidate bill: `max(0, Jtd - PreviouslyInvoiced)`.
- `OverBilled` per line: `max(0, PreviouslyInvoiced - Earned)`. Informational only; it is
  not a flag and does not drive the action.
- `JtdVsEarned` per line: `max(0, Jtd - Earned)`. For Time-and-expense lines this is the
  under-recognition / unbilled-cost amount. It is 0 on fully invoiced lines.
- A line with `PreviouslyInvoicedPct >= 100` is fully invoiced: `BillNow` is 0, the
  Time-and-expense unbilled-cost flag is suppressed, and `JtdVsEarned` is 0.
- Any line whose `JtdVsEarned` exceeds `-Threshold` raises the project-level flag
  `JTD spent exceeds earned revenue by $X`. This applies to Progress and T&E lines alike.
  T&E lines also carry a line-level `Unbilled cost $X` flag. The project-level flag drives
  `REVIEW` when no candidate bill makes the project `BILL NOW`.
- `CurrentBillingInvoice > 0` is structural: it flags regardless of threshold. It does not
  cap the action, so a candidate bill can still make the project `BILL NOW`.
- The document total row is compared to the summed lines within $0.01 per column. A
  mismatch adds a `Totals mismatch` flag and forces the project to `REVIEW` (unless
  `ERROR`).
- Action order: `ERROR` (parse errors) → `REVIEW` (totals mismatch cap) → `BILL NOW` (any
  candidate bill above threshold) → `REVIEW` (any flag) → `NO ACTION`.

### `Import-PreBills -InputPath <folder> [-PdfToTextPath <exe>] [-Threshold <decimal>]`

Extracts, parses, and measures every `*.pdf` in the folder, sorted by name. A per-file
failure becomes an `ERROR` project with a newline-collapsed message; other files continue.
After measuring, documents sharing `BillingScheduleId` + `ToDate` receive a duplicate flag
and are raised to at least `REVIEW`. Returns the project array (empty when no PDFs).
Attaches `RawText` to each project for the audit copy.

### `Write-PreBillConsole -Projects <array>`

Prints the ordered project summary (`BILL NOW → REVIEW → NO ACTION → ERROR`) with columns
`ACTION`, `CONTRACT #`, `PROJECT`, `BILL NOW`, `UNBILLED`, `NOTES`; a totals row (bill now
and unbilled); and all lines of each BILL NOW / REVIEW project. The bill-now total sums the
Bill Now column across the report. Project Notes state
`JTD spent exceeds earned revenue by $X` when the Unbilled total is nonzero. Colors are
paired with text labels.

### `Export-PreBillReports -Projects <array> -OutputPath <folder> -RunStamp <string> [-IncludeClean] [-InputFolder <string>] [-Threshold <decimal>]`

Creates `OutputPath\RunStamp\` and writes `summary.csv`, `detail.csv`, `report.html`, and
`extracted\<pdf-name>.txt`. Without `-IncludeClean`, `NO ACTION` projects are omitted from
both CSVs and the HTML. Returns the run directory path. CSVs are UTF-8 with BOM; amounts
are invariant `0.00` strings.

### `New-PreBillHtml -Projects <array> [-Metadata <hashtable>]`

Returns a self-contained HTML string: KPI tiles (to bill now, projects to bill, unbilled
cost, documents parsed), a color-coded project table with `<details>` line detail, and a
rule/legend section. The "to bill now" tile sums the Bill Now column across the report; the
project Notes state `JTD spent exceeds earned revenue by $X` when the Unbilled total is
nonzero. All document text is HTML-encoded with
`[System.Net.WebUtility]::HtmlEncode`. No external CSS or JavaScript is used.

## CSV schemas

`summary.csv` columns:

`Action, BillingScheduleId, ContractNumber, ContractName, LegalEntity, ToDate,
ContractValue, PreviouslyInvoiced, JtdSpentAtBilling, EarnedRevenue, CurrentBillingInvoice,
BillNowTotal, UnbilledProgress, UnbilledTandM, OverBilledTotal, JtdVsEarnedTotal, Flags,
SourceFile`

`detail.csv` columns:

`Action, ContractNumber, ContractName, TaskCode, Description, FeeType, ContractValue,
PreviouslyInvoiced, PreviouslyInvoicedPct, JtdSpentAtBilling, EarnedRevenuePct,
EarnedRevenue, PctToComplete, CurrentBillingInvoice, BillNow, OverBilled, JtdVsEarned,
Flags, SourceFile`

The column between Earned Revenue and Current Billing Invoice is `% To Complete`, as seen
in the wrapped headers of the sample documents.
