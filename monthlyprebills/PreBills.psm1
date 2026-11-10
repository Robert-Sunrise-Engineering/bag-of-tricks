#Requires -Version 7.0
<#
.SYNOPSIS
    Automated monthly pre-bill review for Deltek/Vantagepoint "Pre-billing summary" PDFs.

.DESCRIPTION
    Extracts the structured pre-billing summary from each PDF with pdftotext, parses the
    per-task rows, applies the billing rules, and renders console / CSV / HTML reports.

    Correctness principle: every figure used for a decision is read from the document as
    reported. Earned Revenue is NEVER recomputed from its percentage; the document's own
    rounding is authoritative and recomputing it would create false totals mismatches.
#>

# --- Parsing patterns ---------------------------------------------------------

# Money token: 1,234.56 | (123.45) | -5.00
$script:MoneyToken = '(?:\(\s*-?[\d,]+(?:\.\d+)?\s*\)|-?[\d,]+(?:\.\d+)?)'
# Percent token: 100.00% | 0.00% | -5.00%
$script:PercentToken = '-?\d+(?:\.\d+)?%'

# A data row starts with a dotted task code, then "|", then the description, then the
# fee-type token and eight required money/percent tokens. Requiring all five money tokens
# (and the dotted code) is what keeps group rows such as
#   "PROJ_001 | Project Group Name ..."
# from being counted as data rows: they have no dot in the code and no money tail.
$script:DataRowPattern = "^\s*(?<Code>[\w]+(?:\.[\w]+)+)\s*\|\s*(?<Desc>.*?)\s+(?<FeeType>Progress|Time\s+and\s+expense)\s+(?<ContractValue>$($script:MoneyToken))\s+(?<PreviouslyInvoiced>$($script:MoneyToken))\s+(?<PreviouslyInvoicedPct>$($script:PercentToken))\s+(?<JtdSpentAtBilling>$($script:MoneyToken))\s+(?<EarnedRevenuePct>$($script:PercentToken))\s+(?<EarnedRevenue>$($script:MoneyToken))\s+(?<PctToComplete>$($script:PercentToken))\s+(?<CurrentBillingInvoice>$($script:MoneyToken))\s*$"

# The document total row has five amount-only columns and no task code.
$script:TotalRowPattern = "^\s*(?<ContractValue>$($script:MoneyToken))\s+(?<PreviouslyInvoiced>$($script:MoneyToken))\s+(?<JtdSpentAtBilling>$($script:MoneyToken))\s+(?<EarnedRevenue>$($script:MoneyToken))\s+(?<CurrentBillingInvoice>$($script:MoneyToken))\s*$"

$script:MaxContinuationLines = 5
$script:ActionOrder = @{ 'BILL NOW' = 0; 'REVIEW' = 1; 'NO ACTION' = 2; 'ERROR' = 3 }

# --- Small helpers ------------------------------------------------------------

function ConvertFrom-Money {
    <#
    .SYNOPSIS
        Parse a document money token as an invariant-culture decimal.
    .DESCRIPTION
        Blank/whitespace -> 0. Parentheses mean negative. Commas are ignored.
        Non-numeric text throws, so the caller can record an actionable parse error.
    #>
    [CmdletBinding()]
    [OutputType([decimal])]
    param(
        [AllowNull()]
        [AllowEmptyString()]
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) { return [decimal]0 }

    $v = $Value.Trim()
    $negative = $false
    if ($v -match '^\(\s*(?<inner>.*?)\s*\)$') {
        $negative = $true
        $v = $Matches['inner']
    }
    $v = $v -replace ',', ''

    $result = [decimal]0
    if (-not [decimal]::TryParse($v, [System.Globalization.NumberStyles]::Number,
            [System.Globalization.CultureInfo]::InvariantCulture, [ref]$result)) {
        throw "Cannot parse money value '$Value'"
    }
    if ($negative) { $result = -$result }
    return $result
}

function ConvertFrom-Percent {
    <#
    .SYNOPSIS
        Parse a document percent token (for example "100.00%") as a decimal percentage.
    #>
    [CmdletBinding()]
    [OutputType([decimal])]
    param(
        [AllowNull()]
        [AllowEmptyString()]
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) { return [decimal]0 }
    $v = ($Value.Trim() -replace '%', '') -replace ',', ''
    $result = [decimal]0
    if (-not [decimal]::TryParse($v, [System.Globalization.NumberStyles]::Number,
            [System.Globalization.CultureInfo]::InvariantCulture, [ref]$result)) {
        throw "Cannot parse percent value '$Value'"
    }
    return $result
}

function Format-Money {
    [CmdletBinding()]
    [OutputType([string])]
    param([decimal]$Value)
    return $Value.ToString('#,##0.00', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Format-Percent {
    [CmdletBinding()]
    [OutputType([string])]
    param([decimal]$Value)
    return $Value.ToString('0.00', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Format-CsvAmount {
    # CSV amounts deliberately omit thousands separators so Excel reads them as numbers.
    [CmdletBinding()]
    [OutputType([string])]
    param([decimal]$Value)
    return $Value.ToString('0.00', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Get-HeaderValue {
    <#
    .SYNOPSIS
        Read a labelled header value from the two-column header block.
    .DESCRIPTION
        When -NextLabel is supplied the value stops at two or more spaces followed by the
        next label. This matters for "Contract name", whose text shares its line with
        "To date" and can itself contain single or run-together spaces.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string]$Text,
        [Parameter(Mandatory)][string]$StartLabel,
        [string]$NextLabel
    )

    $start = [regex]::Escape($StartLabel)
    # Header labels live in two columns: some start the line, others (Legal entity,
    # From date, To date) follow the first column's value. Allow either.
    $labelLead = '(?:^|[^\S\r\n]{2,})'
    if ($NextLabel) {
        $next = [regex]::Escape($NextLabel)
        $pattern = "(?m)$labelLead$start[^\S\r\n]*:[^\S\r\n]*(?<v>[^\r\n]*?)[^\S\r\n]{2,}$next[^\S\r\n]*:"
    }
    else {
        $pattern = "(?m)$labelLead$start[^\S\r\n]*:[^\S\r\n]*(?<v>[^\r\n]*)$"
    }

    $m = [regex]::Match($Text, $pattern)
    if ($m.Success) { return $m.Groups['v'].Value.Trim() }
    return $null
}

function New-EmptyHeader {
    [CmdletBinding()]
    param()
    [pscustomobject][ordered]@{
        BillingScheduleId = $null
        ContractNumber    = $null
        ContractName      = $null
        LegalEntity       = $null
        FromDate          = $null
        ToDate            = $null
    }
}

function Add-ProjectFlag {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][pscustomobject]$Project,
        [string]$Flag
    )
    if ([string]::IsNullOrWhiteSpace($Flag)) { return }
    if (@($Project.Flags) -notcontains $Flag) {
        $Project.Flags = @($Project.Flags) + $Flag
    }
}

# --- Extraction ---------------------------------------------------------------

function Resolve-PdfToTextPath {
    <#
    .SYNOPSIS
        Locate pdftotext.exe.
    .DESCRIPTION
        Search order: explicit -Override, PATH, Git for Windows ucrt64, Git for Windows
        usr/bin. Throws an actionable error when none is found.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([string]$Override)

    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($Override)) { $candidates.Add($Override) }

    $cmd = Get-Command pdftotext -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { $candidates.Add($cmd.Source) }

    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles 'Git\ucrt64\bin\pdftotext.exe'))
        $candidates.Add((Join-Path $env:ProgramFiles 'Git\usr\bin\pdftotext.exe'))
    }

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "pdftotext.exe was not found. Install Poppler or Xpdf, or set 'pdfToTextPath' in config.json / config.local.json."
}

function Get-PreBillRawText {
    <#
    .SYNOPSIS
        Extract the table-preserving text of a pre-billing summary PDF.
    .DESCRIPTION
        Runs pdftotext with -table -enc UTF-8 and rejects output that does not contain the
        "Pre-billing summary" marker. A scanned/image-only PDF can make pdftotext exit 0
        while producing no usable text, so the exit code alone is not sufficient.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$PdfToTextPath
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "PDF not found: $Path"
    }

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $PdfToTextPath
    foreach ($arg in @('-table', '-enc', 'UTF-8', $Path, '-')) { $psi.ArgumentList.Add($arg) }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $psi.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)

    $proc = [System.Diagnostics.Process]::Start($psi)
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()

    if ($proc.ExitCode -ne 0) {
        throw "pdftotext failed (exit $($proc.ExitCode)) for '$([System.IO.Path]::GetFileName($Path))': $($stderr.Trim())"
    }

    $text = $stdout.TrimStart([char]0xFEFF)
    if ($text -notmatch 'Pre-billing summary') {
        throw "Extracted text from '$([System.IO.Path]::GetFileName($Path))' does not contain 'Pre-billing summary'. The PDF appears to be image-only or is not a pre-billing summary."
    }
    return $text
}

# --- Parsing ------------------------------------------------------------------

function ConvertFrom-PreBillText {
    <#
    .SYNOPSIS
        Parse pre-billing summary text into Header, Lines, Totals and ParseErrors.
    .DESCRIPTION
        Parsing principles:
        * Read the figures the document reports; never recompute Earned Revenue.
        * Data rows are identified by a dotted task code plus the full money/percent tail.
        * Group rows (which may contain an underscore and a pipe) are not data rows.
        * Description continuations are joined structurally, skipping blank spacer lines,
          up to five lines. Exceeding the cap records a ParseError rather than truncating
          silently.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Text,
        [string]$SourceFile
    )

    $parseErrors = [System.Collections.Generic.List[string]]::new()
    $lines = [System.Collections.Generic.List[object]]::new()
    $totals = $null
    $header = New-EmptyHeader

    # Normalize line endings and form feeds; strip a leading BOM if present.
    $normalized = $Text -replace "`r`n", "`n" -replace "`r", "`n" -replace "`f", "`n"
    $normalized = $normalized.TrimStart([char]0xFEFF)

    if ($normalized -notmatch 'Pre-billing summary') {
        $parseErrors.Add('PDF appears to be image-only or is not a pre-billing summary (marker "Pre-billing summary" not found).')
        return [pscustomobject][ordered]@{
            Header      = $header
            Lines       = @()
            Totals      = $null
            ParseErrors = @($parseErrors)
            SourceFile  = $SourceFile
        }
    }

    # The progress table is what matters; the transactions section is not used.
    $transactionsIndex = $normalized.IndexOf('Transactions summary')
    if ($transactionsIndex -ge 0) { $normalized = $normalized.Substring(0, $transactionsIndex) }

    # --- Header (everything before Progress summary) ---
    $progressIndex = $normalized.IndexOf('Progress summary')
    if ($progressIndex -lt 0) {
        $parseErrors.Add('Progress summary section not found.')
        $headerText = $normalized
    }
    else {
        $headerText = $normalized.Substring(0, $progressIndex)
    }

    $header.BillingScheduleId = Get-HeaderValue -Text $headerText -StartLabel 'Billing schedule Id' -NextLabel 'Legal entity'
    $header.ContractNumber = Get-HeaderValue -Text $headerText -StartLabel 'Contract number' -NextLabel 'From date'
    $header.ContractName = Get-HeaderValue -Text $headerText -StartLabel 'Contract name' -NextLabel 'To date'
    $header.LegalEntity = Get-HeaderValue -Text $headerText -StartLabel 'Legal entity'
    $header.FromDate = Get-HeaderValue -Text $headerText -StartLabel 'From date'
    $header.ToDate = Get-HeaderValue -Text $headerText -StartLabel 'To date'

    # Contract names can wrap onto a following indented, colon-free, pipe-free line.
    if ($header.ContractName) {
        $headerLines = $headerText -split "`n"
        $nameLineIndex = -1
        for ($n = 0; $n -lt $headerLines.Count; $n++) {
            if ($headerLines[$n] -match '^\s*Contract name\s*:') { $nameLineIndex = $n; break }
        }
        if ($nameLineIndex -ge 0) {
            for ($n = $nameLineIndex + 1; $n -lt $headerLines.Count; $n++) {
                $candidate = $headerLines[$n]
                if ($candidate.Trim() -eq '') { continue }
                if ($candidate -match '^\s' -and $candidate -notmatch ':' -and $candidate -notmatch '\|') {
                    $header.ContractName = $header.ContractName + ' ' + $candidate.Trim()
                }
                else {
                    break
                }
            }
        }
        # The PDF uses justification runs inside names; collapse them for display and reports.
        $header.ContractName = ([regex]::Replace($header.ContractName, '\s+', ' ')).Trim()
    }

    $requiredFields = [ordered]@{
        'Billing schedule Id' = $header.BillingScheduleId
        'Contract number'     = $header.ContractNumber
        'Contract name'       = $header.ContractName
        'Legal entity'        = $header.LegalEntity
        'To date'             = $header.ToDate
    }
    foreach ($field in $requiredFields.Keys) {
        if ([string]::IsNullOrWhiteSpace([string]$requiredFields[$field])) {
            $parseErrors.Add("Missing required header field: $field")
        }
    }
    # From date is intentionally optional: it is blank in every current document.

    if ($progressIndex -lt 0) {
        return [pscustomobject][ordered]@{
            Header      = $header
            Lines       = @()
            Totals      = $null
            ParseErrors = @($parseErrors)
            SourceFile  = $SourceFile
        }
    }

    # --- Progress table rows ---
    $allLines = $normalized -split "`n"
    $progressLineIndex = 0
    for ($n = 0; $n -lt $allLines.Count; $n++) {
        if ($allLines[$n] -match '^\s*Progress summary\s*$') { $progressLineIndex = $n; break }
    }

    $i = $progressLineIndex + 1
    while ($i -lt $allLines.Count) {
        $rawLine = $allLines[$i]
        $trimmed = $rawLine.Trim()

        if ($trimmed -eq '') { $i++; continue }

        $dataMatch = [regex]::Match($rawLine, $script:DataRowPattern)
        if ($dataMatch.Success) {
            try {
                $feeType = ([regex]::Replace($dataMatch.Groups['FeeType'].Value, '\s+', ' ')).Trim()
                $description = ([regex]::Replace($dataMatch.Groups['Desc'].Value, '\s+', ' ')).Trim()

                $lineNumber = $i + 1
                $j = $i + 1
                $continuationCount = 0
                while ($j -lt $allLines.Count) {
                    $contRaw = $allLines[$j]
                    $contTrimmed = $contRaw.Trim()

                    if ($contTrimmed -eq '') { $j++; continue }
                    # A repeated page header must not be glued onto a description. The rest of
                    # that header block carries colons, so it stops on the next line.
                    if ($contTrimmed -eq 'Transactions summary' -or $contTrimmed -eq 'Progress summary' -or $contTrimmed -eq 'Pre-billing summary') { break }
                    if ([regex]::IsMatch($contRaw, $script:DataRowPattern)) { break }
                    if ([regex]::IsMatch($contRaw, $script:TotalRowPattern)) { break }
                    if ($contRaw.Contains('|') -or $contRaw.Contains(':')) { break }
                    if ($contTrimmed -in @('Complete', 'Status', 'Nonchargeable') -or $contTrimmed.StartsWith('Nonchargea')) { $j++; continue }

                    if ($continuationCount -ge $script:MaxContinuationLines) {
                        $parseErrors.Add("Line $($j + 1): description continuation exceeds $($script:MaxContinuationLines) lines; refusing to truncate silently.")
                        break
                    }

                    # Join a mid-word split without a space: "Multi-" + "Tenant Widget Support"
                    # becomes "Multi-Tenant Widget Support". A spaced dash ("Apr 1 -" + "Sep 30")
                    # keeps the space.
                    $joinWithoutSpace = $description.EndsWith('-') -and
                        $description.Length -gt 1 -and
                        -not [char]::IsWhiteSpace($description[$description.Length - 2])
                    if ($joinWithoutSpace) {
                        $description = $description + $contTrimmed
                    }
                    elseif ([string]::IsNullOrEmpty($description)) {
                        # Some reports leave the description cell empty and put the whole
                        # text on the continuation line; do not introduce a leading space.
                        $description = $contTrimmed
                    }
                    else {
                        $description = $description + ' ' + $contTrimmed
                    }
                    $continuationCount++
                    $j++
                }

                $description = $description.Trim()

                $lines.Add([pscustomobject][ordered]@{
                    TaskCode              = $dataMatch.Groups['Code'].Value
                    Description           = $description
                    FeeType               = $feeType
                    ContractValue         = ConvertFrom-Money $dataMatch.Groups['ContractValue'].Value
                    PreviouslyInvoiced    = ConvertFrom-Money $dataMatch.Groups['PreviouslyInvoiced'].Value
                    PreviouslyInvoicedPct = ConvertFrom-Percent $dataMatch.Groups['PreviouslyInvoicedPct'].Value
                    JtdSpentAtBilling     = ConvertFrom-Money $dataMatch.Groups['JtdSpentAtBilling'].Value
                    EarnedRevenuePct      = ConvertFrom-Percent $dataMatch.Groups['EarnedRevenuePct'].Value
                    EarnedRevenue         = ConvertFrom-Money $dataMatch.Groups['EarnedRevenue'].Value
                    PctToComplete         = ConvertFrom-Percent $dataMatch.Groups['PctToComplete'].Value
                    CurrentBillingInvoice = ConvertFrom-Money $dataMatch.Groups['CurrentBillingInvoice'].Value
                    SourceLineNumber      = $lineNumber
                })

                $i = $j
                continue
            }
            catch {
                $parseErrors.Add("Line $($i + 1): could not parse task row: $($_.Exception.Message)")
                $i++
                continue
            }
        }

        $totalMatch = [regex]::Match($rawLine, $script:TotalRowPattern)
        if ($totalMatch.Success) {
            if ($null -eq $totals) {
                $totals = [pscustomobject][ordered]@{
                    ContractValue         = ConvertFrom-Money $totalMatch.Groups['ContractValue'].Value
                    PreviouslyInvoiced    = ConvertFrom-Money $totalMatch.Groups['PreviouslyInvoiced'].Value
                    JtdSpentAtBilling     = ConvertFrom-Money $totalMatch.Groups['JtdSpentAtBilling'].Value
                    EarnedRevenue         = ConvertFrom-Money $totalMatch.Groups['EarnedRevenue'].Value
                    CurrentBillingInvoice = ConvertFrom-Money $totalMatch.Groups['CurrentBillingInvoice'].Value
                    SourceLineNumber      = $i + 1
                }
            }
            $i++
            continue
        }

        # A dotted task code followed by a pipe that did not match the full row pattern is
        # template drift. Never guess: record the file/line/text and mark the document ERROR.
        if ($rawLine -match '^\s*[\w]+(?:\.[\w]+)+\s*\|') {
            $parseErrors.Add("Line $($i + 1): task row could not be parsed (template drift?): $($trimmed)")
        }

        $i++
    }

    if ($lines.Count -eq 0 -and $parseErrors.Count -eq 0) {
        $parseErrors.Add('No task rows were found in the Progress summary section.')
    }
    if ($null -eq $totals -and $lines.Count -gt 0) {
        $parseErrors.Add('Document total row was not found.')
    }

    return [pscustomobject][ordered]@{
        Header      = $header
        Lines       = @($lines)
        Totals      = $totals
        ParseErrors = @($parseErrors)
        SourceFile  = $SourceFile
    }
}

# --- Rules / roll-up ----------------------------------------------------------

function Measure-PreBill {
    <#
    .SYNOPSIS
        Apply the per-line billing rules and roll the result up to a project verdict.
    .DESCRIPTION
        Per line:
          Progress:          bill = max(0, Earned Revenue - Previously Invoiced)
          Time and expense:  bill = max(0, JTD Spent at Billing - Previously Invoiced)
          Over-billed        = max(0, Previously Invoiced - Earned Revenue)
          JTD vs earned      = max(0, JTD Spent at Billing - Earned Revenue)

        Only under-billing and under-recognition matter for the verdict. Over-billed amounts
        are still computed and written to the CSV data, but they are not review flags and do
        not drive an action. A line whose Previously Invoiced % is at 100% is fully billed:
        it produces no candidate bill and no under-recognition flag. A totals mismatch forces
        REVIEW (unless ERROR) because the parsed figures cannot be trusted. Structural flags
        (totals mismatch, current invoice already queued, parse errors) always fire.
        -Threshold gates amount-based decisions only.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][pscustomobject]$Parsed,
        [decimal]$Threshold = 0.01
    )

    $header = $Parsed.Header
    $parseErrors = @($Parsed.ParseErrors)
    $sourceFile = $Parsed.SourceFile
    $projectFlags = [System.Collections.Generic.List[string]]::new()
    $anyUnbilledOverThreshold = $false

    $measuredLines = [System.Collections.Generic.List[object]]::new()

    foreach ($line in @($Parsed.Lines)) {
        $lineFlags = [System.Collections.Generic.List[string]]::new()

        # A line at 100% previously invoiced is fully billed. It must not produce a bill or an
        # under-recognition finding, even if rounding makes earned or JTD exceed invoiced.
        $fullyInvoiced = $line.PreviouslyInvoicedPct -ge 100

        if ($line.FeeType -eq 'Progress') {
            $billNow = [decimal]0
            if (-not $fullyInvoiced -and ($line.EarnedRevenue - $line.PreviouslyInvoiced) -gt 0) {
                $billNow = $line.EarnedRevenue - $line.PreviouslyInvoiced
            }
            # Over-billing is not a finding in this process. The over-billed amount is still
            # calculated below for the CSV data, but it does not raise a flag or affect action.
        }
        else {
            $billNow = [decimal]0
            if (-not $fullyInvoiced -and ($line.JtdSpentAtBilling - $line.PreviouslyInvoiced) -gt 0) {
                $billNow = $line.JtdSpentAtBilling - $line.PreviouslyInvoiced
            }
            if (-not $fullyInvoiced -and ($line.JtdSpentAtBilling - $line.EarnedRevenue) -gt $Threshold) {
                # Costs incurred exceed recognized revenue: under-recognition, which matters.
                $lineFlags.Add("Unbilled cost `$$(Format-Money ($line.JtdSpentAtBilling - $line.EarnedRevenue))")
            }
        }

        $overBilled = [decimal]0
        if (($line.PreviouslyInvoiced - $line.EarnedRevenue) -gt 0) {
            $overBilled = $line.PreviouslyInvoiced - $line.EarnedRevenue
        }

        $jtdVsEarned = [decimal]0
        if (-not $fullyInvoiced -and ($line.JtdSpentAtBilling - $line.EarnedRevenue) -gt 0) {
            $jtdVsEarned = $line.JtdSpentAtBilling - $line.EarnedRevenue
        }
        if ($jtdVsEarned -gt $Threshold) { $anyUnbilledOverThreshold = $true }

        if ($line.CurrentBillingInvoice -gt 0) {
            $lineFlags.Add("Current billing invoice `$$(Format-Money $line.CurrentBillingInvoice) already queued")
        }

        $lineAction = 'NO ACTION'
        if ($billNow -gt $Threshold) { $lineAction = 'BILL NOW' }
        elseif ($lineFlags.Count -gt 0) { $lineAction = 'REVIEW' }

        foreach ($f in $lineFlags) {
            # Keep the T&M line-level "Unbilled cost" flag in the line detail only. The
            # project gets one clear JTD-over-earned total below, covering every fee type.
            if ($f -like 'Unbilled cost *') { continue }
            if (-not $projectFlags.Contains($f)) { $projectFlags.Add($f) }
        }

        $measuredLines.Add([pscustomobject][ordered]@{
            Action                   = $lineAction
            TaskCode                 = $line.TaskCode
            Description              = $line.Description
            FeeType                  = $line.FeeType
            ContractValue            = $line.ContractValue
            PreviouslyInvoiced       = $line.PreviouslyInvoiced
            PreviouslyInvoicedPct    = $line.PreviouslyInvoicedPct
            JtdSpentAtBilling        = $line.JtdSpentAtBilling
            EarnedRevenuePct         = $line.EarnedRevenuePct
            EarnedRevenue            = $line.EarnedRevenue
            PctToComplete            = $line.PctToComplete
            CurrentBillingInvoice    = $line.CurrentBillingInvoice
            BillNow                  = $billNow
            OverBilled               = $overBilled
            JtdVsEarned              = $jtdVsEarned
            Flags                    = @($lineFlags)
            SourceLineNumber         = $line.SourceLineNumber
        })
    }

    # --- Totals cross-check (structural; not gated by threshold) ---
    $sumContract = [decimal]0
    $sumPrev = [decimal]0
    $sumJtd = [decimal]0
    $sumEarned = [decimal]0
    $sumCurrent = [decimal]0
    $sumBillNow = [decimal]0
    $sumUnbilledProgress = [decimal]0
    $sumUnbilledTandM = [decimal]0
    $sumOverBilled = [decimal]0
    $sumJtdVsEarned = [decimal]0

    foreach ($line in $measuredLines) {
        $sumContract += $line.ContractValue
        $sumPrev += $line.PreviouslyInvoiced
        $sumJtd += $line.JtdSpentAtBilling
        $sumEarned += $line.EarnedRevenue
        $sumCurrent += $line.CurrentBillingInvoice
        $sumBillNow += $line.BillNow
        if ($line.FeeType -eq 'Progress') { $sumUnbilledProgress += $line.BillNow }
        else { $sumUnbilledTandM += $line.BillNow }
        $sumOverBilled += $line.OverBilled
        $sumJtdVsEarned += $line.JtdVsEarned
    }

    if ($anyUnbilledOverThreshold) {
        # Project-level under-recognition finding, visible in the console/HTML Notes column.
        $flag = "JTD spent exceeds earned revenue by `$$(Format-Money $sumJtdVsEarned)"
        if (-not $projectFlags.Contains($flag)) { $projectFlags.Add($flag) }
    }

    $totalsMismatch = $false
    $reported = @{
        ContractValue         = $sumContract
        PreviouslyInvoiced    = $sumPrev
        JtdSpentAtBilling     = $sumJtd
        EarnedRevenue         = $sumEarned
        CurrentBillingInvoice = $sumCurrent
    }

    if ($null -ne $Parsed.Totals) {
        $reported.ContractValue = $Parsed.Totals.ContractValue
        $reported.PreviouslyInvoiced = $Parsed.Totals.PreviouslyInvoiced
        $reported.JtdSpentAtBilling = $Parsed.Totals.JtdSpentAtBilling
        $reported.EarnedRevenue = $Parsed.Totals.EarnedRevenue
        $reported.CurrentBillingInvoice = $Parsed.Totals.CurrentBillingInvoice

        $sums = @{
            ContractValue         = $sumContract
            PreviouslyInvoiced    = $sumPrev
            JtdSpentAtBilling     = $sumJtd
            EarnedRevenue         = $sumEarned
            CurrentBillingInvoice = $sumCurrent
        }
        foreach ($column in @('ContractValue', 'PreviouslyInvoiced', 'JtdSpentAtBilling', 'EarnedRevenue', 'CurrentBillingInvoice')) {
            $expected = [decimal]$reported[$column]
            $actual = [decimal]$sums[$column]
            if ([Math]::Abs($expected - $actual) -gt 0.01) {
                $totalsMismatch = $true
                $flag = "Totals mismatch: $column expected `$$(Format-Money $expected), summed `$$(Format-Money $actual)"
                if (-not $projectFlags.Contains($flag)) { $projectFlags.Add($flag) }
            }
        }

        if ($Parsed.Totals.CurrentBillingInvoice -gt 0) {
            $flag = "Current billing invoice `$$(Format-Money $Parsed.Totals.CurrentBillingInvoice) already queued"
            if (-not $projectFlags.Contains($flag)) { $projectFlags.Add($flag) }
        }
    }

    # --- Project verdict ---
    $action = 'NO ACTION'
    if ($parseErrors.Count -gt 0) {
        $action = 'ERROR'
    }
    elseif ($totalsMismatch) {
        # Explicitly cap at REVIEW: untrusted figures must not silently become BILL NOW.
        $action = 'REVIEW'
    }
    else {
        $anyBill = $false
        foreach ($line in $measuredLines) { if ($line.BillNow -gt $Threshold) { $anyBill = $true; break } }
        if ($anyBill) { $action = 'BILL NOW' }
        elseif ($projectFlags.Count -gt 0) { $action = 'REVIEW' }
    }

    $flags = @($projectFlags)
    foreach ($parseError in $parseErrors) {
        $flag = "Parse error: $parseError"
        if ($flags -notcontains $flag) { $flags += $flag }
    }

    return [pscustomobject][ordered]@{
        Action                = $action
        BillingScheduleId     = $header.BillingScheduleId
        ContractNumber        = $header.ContractNumber
        ContractName          = $header.ContractName
        LegalEntity           = $header.LegalEntity
        FromDate              = $header.FromDate
        ToDate                = $header.ToDate
        ContractValue         = [decimal]$reported.ContractValue
        PreviouslyInvoiced    = [decimal]$reported.PreviouslyInvoiced
        JtdSpentAtBilling     = [decimal]$reported.JtdSpentAtBilling
        EarnedRevenue         = [decimal]$reported.EarnedRevenue
        CurrentBillingInvoice = [decimal]$reported.CurrentBillingInvoice
        BillNowTotal          = $sumBillNow
        UnbilledProgress      = $sumUnbilledProgress
        UnbilledTandM         = $sumUnbilledTandM
        OverBilledTotal       = $sumOverBilled
        JtdVsEarnedTotal      = $sumJtdVsEarned
        Flags                 = $flags
        SourceFile            = $sourceFile
        Lines                 = @($measuredLines)
        Totals                = $Parsed.Totals
        ParseErrors           = $parseErrors
        RawText               = ''
    }
}

function New-ErrorProject {
    [CmdletBinding()]
    param(
        [string]$SourceFile,
        [string]$Message
    )
    [pscustomobject][ordered]@{
        Action                = 'ERROR'
        BillingScheduleId     = $null
        ContractNumber        = $null
        ContractName          = $null
        LegalEntity           = $null
        FromDate              = $null
        ToDate                = $null
        ContractValue         = [decimal]0
        PreviouslyInvoiced    = [decimal]0
        JtdSpentAtBilling     = [decimal]0
        EarnedRevenue         = [decimal]0
        CurrentBillingInvoice = [decimal]0
        BillNowTotal          = [decimal]0
        UnbilledProgress      = [decimal]0
        UnbilledTandM         = [decimal]0
        OverBilledTotal       = [decimal]0
        JtdVsEarnedTotal      = [decimal]0
        Flags                 = @("Parse error: $Message")
        SourceFile            = $SourceFile
        Lines                 = @()
        Totals                = $null
        ParseErrors           = @($Message)
        RawText               = ''
    }
}

# --- Orchestration ------------------------------------------------------------

function Import-PreBills {
    <#
    .SYNOPSIS
        Extract, parse and measure every PDF in a folder.
    .DESCRIPTION
        Per-file failures become ERROR project objects with an actionable message and do
        not stop the rest of the batch. Duplicate BillingScheduleId + ToDate documents are
        kept but flagged so a month is not silently double-billed.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$InputPath,
        [string]$PdfToTextPath,
        [decimal]$Threshold = 0.01
    )

    if (-not $PdfToTextPath) { $PdfToTextPath = Resolve-PdfToTextPath }
    if (-not (Test-Path -LiteralPath $InputPath -PathType Container)) {
        throw "Input folder not found: $InputPath"
    }

    $pdfs = @(Get-ChildItem -LiteralPath $InputPath -Filter '*.pdf' -File | Sort-Object Name)
    if ($pdfs.Count -eq 0) { return @() }

    $projects = [System.Collections.Generic.List[object]]::new()
    foreach ($pdf in $pdfs) {
        try {
            $rawText = Get-PreBillRawText -Path $pdf.FullName -PdfToTextPath $PdfToTextPath
            $parsed = ConvertFrom-PreBillText -Text $rawText -SourceFile $pdf.Name
            $project = Measure-PreBill -Parsed $parsed -Threshold $Threshold
            $project.RawText = $rawText
            $projects.Add($project)
        }
        catch {
            $message = ($_.Exception.Message -replace '\s*\r?\n\s*', ' ').Trim()
            $projects.Add((New-ErrorProject -SourceFile $pdf.Name -Message $message))
        }
    }

    # Duplicate schedule + date detection across the batch.
    $duplicateGroups = $projects |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_.BillingScheduleId) -and -not [string]::IsNullOrWhiteSpace($_.ToDate) } |
        Group-Object { "$($_.BillingScheduleId)|$($_.ToDate)" }

    foreach ($group in $duplicateGroups) {
        if ($group.Count -gt 1) {
            foreach ($project in $group.Group) {
                Add-ProjectFlag -Project $project -Flag "Duplicate billing schedule/date ($($group.Count) documents)"
                if ($project.Action -eq 'NO ACTION') { $project.Action = 'REVIEW' }
            }
        }
    }

    return @($projects)
}

# --- Console output -----------------------------------------------------------

function Write-PreBillConsole {
    <#
    .SYNOPSIS
        Print the exception-focused project summary and line detail.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Projects)

    if (-not $Projects -or $Projects.Count -eq 0) {
        Write-Host 'No pre-bill documents were processed.' -ForegroundColor Yellow
        return
    }

    $sorted = $Projects | Sort-Object `
        @{ Expression = { $script:ActionOrder[$_.Action] } }, `
        @{ Expression = { $_.ContractNumber } }

    $colors = @{
        'BILL NOW'  = 'Red'
        'REVIEW'    = 'DarkYellow'
        'NO ACTION' = 'Green'
        'ERROR'     = 'Magenta'
    }

    Write-Host ''
    Write-Host ('{0,-10} {1,-11} {2,-44} {3,10} {4,12}  {5}' -f
        'ACTION', 'CONTRACT #', 'PROJECT', 'BILL NOW', 'UNBILLED', 'NOTES')
    Write-Host ('-' * 140)

    foreach ($project in $sorted) {
        $name = [string]$project.ContractName
        if ($name.Length -gt 44) { $name = $name.Substring(0, 41) + '...' }
        $notes = if ($project.Action -eq 'ERROR') { (@($project.ParseErrors) -join '; ') }
        else { (@($project.Flags) -join '; ') }
        if ([string]::IsNullOrWhiteSpace($notes)) { $notes = '-' }

        $color = $colors[$project.Action]
        if (-not $color) { $color = 'Gray' }

        Write-Host ('{0,-10} {1,-11} {2,-44} {3,10} {4,12}  {5}' -f
            $project.Action, $project.ContractNumber, $name,
            "`$$(Format-Money $project.BillNowTotal)",
            "`$$(Format-Money $project.JtdVsEarnedTotal)", $notes) -ForegroundColor $color
    }

    $totalBill = [decimal]0
    foreach ($project in $Projects) { $totalBill += [decimal]$project.BillNowTotal }
    $totalUnbilled = [decimal]0
    foreach ($project in $Projects) { $totalUnbilled += [decimal]$project.JtdVsEarnedTotal }
    Write-Host ('-' * 140)
    Write-Host ('{0,-10} {1,-11} {2,-44} {3,10} {4,12}  {5}' -f
        'TOTAL', "$($Projects.Count) documents", '',
        "`$$(Format-Money $totalBill)",
        "`$$(Format-Money $totalUnbilled)",
        'Unbilled = JTD spent above earned revenue') -ForegroundColor Cyan

    foreach ($project in $sorted | Where-Object { $_.Action -in @('BILL NOW', 'REVIEW') }) {
        Write-Host ''
        Write-Host ("$($project.Action): $($project.ContractNumber) $($project.ContractName)") -ForegroundColor $colors[$project.Action]
        foreach ($line in @($project.Lines)) {
            $marker = if ($line.Action -ne 'NO ACTION') { '*' } else { ' ' }
            $lineNotes = (@($line.Flags) -join '; ')
            if ($line.BillNow -gt 0) {
                if ($lineNotes) { $lineNotes = "Bill `$$(Format-Money $line.BillNow); $lineNotes" }
                else { $lineNotes = "Bill `$$(Format-Money $line.BillNow)" }
            }
            $lineDescription = [string]$line.Description
            if ($lineDescription.Length -gt 40) { $lineDescription = $lineDescription.Substring(0, 37) + '...' }
            Write-Host ('  {0} {1,-22} {2,-40} {3,-16} {4,10} {5,10}  {6}' -f
                $marker, $line.TaskCode, $lineDescription, $line.FeeType,
                "`$$(Format-Money $line.BillNow)", "`$$(Format-Money $line.JtdVsEarned)", $lineNotes)
        }
    }
    Write-Host ''
}

# --- CSV / HTML reports -------------------------------------------------------

function Write-CsvFile {
    [CmdletBinding()]
    param(
        [AllowEmptyCollection()][object[]]$Rows,
        [Parameter(Mandatory)][string[]]$Columns,
        [Parameter(Mandatory)][string]$Path
    )

    $header = (@($Columns | ForEach-Object { '"' + ($_ -replace '"', '""') + '"' }) -join ',')
    $allLines = [System.Collections.Generic.List[string]]::new()
    $allLines.Add($header)
    if ($Rows -and $Rows.Count -gt 0) {
        # Pipe (not -InputObject) so the collection is enumerated one row at a time.
        $csv = @($Rows | Select-Object -Property $Columns | ConvertTo-Csv -NoTypeInformation)
        for ($i = 1; $i -lt $csv.Count; $i++) { $allLines.Add([string]$csv[$i]) }
    }
    [System.IO.File]::WriteAllLines($Path, $allLines, [System.Text.UTF8Encoding]::new($true))
}

function New-PreBillHtml {
    <#
    .SYNOPSIS
        Build the self-contained one-page HTML report.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Projects,
        [hashtable]$Metadata = @{}
    )

    $enc = { param($value) [System.Net.WebUtility]::HtmlEncode([string]$value) }

    $sorted = @($Projects | Sort-Object `
        @{ Expression = { $script:ActionOrder[$_.Action] } }, `
        @{ Expression = { $_.ContractNumber } })

    $totalBill = [decimal]0
    $totalUnbilled = [decimal]0
    $billNowCount = 0
    $parsedCount = 0
    foreach ($p in $sorted) {
        $totalBill += [decimal]$p.BillNowTotal
        $totalUnbilled += [decimal]$p.JtdVsEarnedTotal
        if ($p.Action -eq 'BILL NOW') { $billNowCount++ }
        if ($p.Action -ne 'ERROR') { $parsedCount++ }
    }
    # The report may omit NO ACTION rows; the document counts should still describe the run.
    $totalDocuments = if ($Metadata.ContainsKey('TotalDocuments')) { [int]$Metadata['TotalDocuments'] } else { $sorted.Count }
    $parsedDocuments = if ($Metadata.ContainsKey('ParsedDocuments')) { [int]$Metadata['ParsedDocuments'] } else { $parsedCount }

    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.AppendLine('<!DOCTYPE html>')
    [void]$sb.AppendLine('<html lang="en">')
    [void]$sb.AppendLine('<head>')
    [void]$sb.AppendLine('<meta charset="utf-8">')
    [void]$sb.AppendLine('<meta name="viewport" content="width=device-width, initial-scale=1">')
    [void]$sb.AppendLine('<title>Monthly Pre-Bill Review</title>')
    [void]$sb.AppendLine('<style>')
    [void]$sb.AppendLine(@'
:root {
  --ink: #1f2937; --muted: #6b7280; --line: #d1d5db; --panel: #f9fafb;
  --bill: #b91c1c; --review: #b45309; --clean: #15803d; --error: #6d28d9;
}
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; color: var(--ink); background: #fff; line-height: 1.45; }
h1 { margin: 0 0 .25rem; font-size: 1.6rem; }
h2 { margin: 2rem 0 .75rem; font-size: 1.15rem; }
.meta { margin: 0; color: var(--muted); font-size: .9rem; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin: 1.5rem 0 2rem; }
.kpi { border: 1px solid var(--line); border-radius: 8px; padding: 1rem 1.15rem; background: var(--panel); }
.kpi-label { color: var(--muted); font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; }
.kpi-value { font-size: 1.6rem; font-weight: 600; margin-top: .2rem; }
table { width: 100%; border-collapse: collapse; font-size: .9rem; }
th, td { padding: .5rem .55rem; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { background: var(--panel); font-size: .78rem; text-transform: uppercase; letter-spacing: .03em; color: var(--muted); }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.badge { display: inline-block; padding: .12rem .5rem; border-radius: 999px; font-size: .75rem; font-weight: 600; color: #fff; white-space: nowrap; }
.billnow { background: var(--bill); }
.review { background: var(--review); }
.noaction { background: var(--clean); }
.error { background: var(--error); }
details { margin: .15rem 0; }
summary { cursor: pointer; color: #1d4ed8; font-size: .85rem; }
.detail-table { margin: .5rem 0 1rem; font-size: .82rem; }
.detail-table th, .detail-table td { padding: .3rem .45rem; }
.notes { color: var(--muted); font-size: .82rem; }
.rules { margin-top: 2.5rem; border-top: 1px solid var(--line); padding-top: 1rem; color: #374151; font-size: .88rem; }
.rules code { background: var(--panel); padding: .05rem .3rem; border-radius: 4px; }
@media print { body { padding: 0; } .kpis { break-inside: avoid; } details { display: block; } }
'@)
    [void]$sb.AppendLine('</style>')
    [void]$sb.AppendLine('</head>')
    [void]$sb.AppendLine('<body>')

    $generatedAt = if ($Metadata.ContainsKey('GeneratedAt')) { $Metadata['GeneratedAt'] } else { (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') }
    $inputFolder = if ($Metadata.ContainsKey('InputFolder')) { $Metadata['InputFolder'] } else { '' }
    $threshold = if ($Metadata.ContainsKey('Threshold')) { $Metadata['Threshold'] } else { '0.01' }
    $stamp = if ($Metadata.ContainsKey('RunStamp')) { $Metadata['RunStamp'] } else { '' }

    [void]$sb.AppendLine('<header>')
    [void]$sb.AppendLine('<h1>Monthly Pre-Bill Review</h1>')
    [void]$sb.AppendLine("<p class=""meta"">Generated $(& $enc $generatedAt) &middot; Input: <code>$(& $enc $inputFolder)</code> &middot; Threshold: `$$(& $enc $threshold) &middot; Run: $(& $enc $stamp)</p>")
    [void]$sb.AppendLine('</header>')

    [void]$sb.AppendLine('<section class="kpis">')
    [void]$sb.AppendLine("<div class=""kpi""><div class=""kpi-label"">To bill now</div><div class=""kpi-value"">`$$(Format-Money $totalBill)</div></div>")
    [void]$sb.AppendLine("<div class=""kpi""><div class=""kpi-label"">Projects to bill</div><div class=""kpi-value"">$billNowCount</div></div>")
    [void]$sb.AppendLine("<div class=""kpi""><div class=""kpi-label"">Unbilled cost</div><div class=""kpi-value"">`$$(Format-Money $totalUnbilled)</div></div>")
    [void]$sb.AppendLine("<div class=""kpi""><div class=""kpi-label"">Documents parsed</div><div class=""kpi-value"">$parsedDocuments of $totalDocuments</div></div>")
    [void]$sb.AppendLine('</section>')

    [void]$sb.AppendLine('<section>')
    [void]$sb.AppendLine('<h2>Projects</h2>')
    [void]$sb.AppendLine('<table class="projects">')
    [void]$sb.AppendLine('<thead><tr><th>Action</th><th>Contract #</th><th>Project</th><th>To date</th><th>Contract value</th><th>Previously invoiced</th><th>JTD spent</th><th>Earned revenue</th><th>Bill now</th><th>Unbilled</th><th>Notes</th></tr></thead>')
    [void]$sb.AppendLine('<tbody>')

    foreach ($p in $sorted) {
        $class = switch ($p.Action) {
            'BILL NOW' { 'billnow' }
            'REVIEW' { 'review' }
            'NO ACTION' { 'noaction' }
            'ERROR' { 'error' }
            default { 'review' }
        }
        $flags = if ($p.Action -eq 'ERROR') { (@($p.ParseErrors) -join '; ') } else { (@($p.Flags) -join '; ') }
        [void]$sb.AppendLine('<tr>')
        [void]$sb.AppendLine("<td><span class=""badge $class"">$(& $enc $p.Action)</span></td>")
        [void]$sb.AppendLine("<td>$(& $enc $p.ContractNumber)</td>")
        [void]$sb.AppendLine("<td>$(& $enc $p.ContractName)</td>")
        [void]$sb.AppendLine("<td>$(& $enc $p.ToDate)</td>")
        [void]$sb.AppendLine("<td class=""num"">$(Format-Money $p.ContractValue)</td>")
        [void]$sb.AppendLine("<td class=""num"">$(Format-Money $p.PreviouslyInvoiced)</td>")
        [void]$sb.AppendLine("<td class=""num"">$(Format-Money $p.JtdSpentAtBilling)</td>")
        [void]$sb.AppendLine("<td class=""num"">$(Format-Money $p.EarnedRevenue)</td>")
        [void]$sb.AppendLine("<td class=""num"">$(Format-Money $p.BillNowTotal)</td>")
        [void]$sb.AppendLine("<td class=""num"">$(Format-Money $p.JtdVsEarnedTotal)</td>")
        [void]$sb.AppendLine("<td class=""notes"">$(& $enc $flags)</td>")
        [void]$sb.AppendLine('</tr>')

        if (@($p.Lines).Count -gt 0) {
            [void]$sb.AppendLine('<tr><td colspan="11">')
            [void]$sb.AppendLine('<details><summary>Line detail</summary>')
            [void]$sb.AppendLine('<table class="detail-table">')
            [void]$sb.AppendLine('<thead><tr><th>Action</th><th>Task code</th><th>Description</th><th>Fee type</th><th>Contract value</th><th>Previously invoiced</th><th>JTD spent</th><th>Earned revenue</th><th>Bill now</th><th>Unbilled</th><th>Flags</th></tr></thead><tbody>')
            foreach ($line in @($p.Lines)) {
                $lineClass = switch ($line.Action) {
                    'BILL NOW' { 'billnow' }
                    'REVIEW' { 'review' }
                    default { 'noaction' }
                }
                $lineFlags = @($line.Flags) -join '; '
                [void]$sb.AppendLine('<tr>')
                [void]$sb.AppendLine("<td><span class=""badge $lineClass"">$(& $enc $line.Action)</span></td>")
                [void]$sb.AppendLine("<td>$(& $enc $line.TaskCode)</td>")
                [void]$sb.AppendLine("<td>$(& $enc $line.Description)</td>")
                [void]$sb.AppendLine("<td>$(& $enc $line.FeeType)</td>")
                [void]$sb.AppendLine("<td class=""num"">$(Format-Money $line.ContractValue)</td>")
                [void]$sb.AppendLine("<td class=""num"">$(Format-Money $line.PreviouslyInvoiced)</td>")
                [void]$sb.AppendLine("<td class=""num"">$(Format-Money $line.JtdSpentAtBilling)</td>")
                [void]$sb.AppendLine("<td class=""num"">$(Format-Money $line.EarnedRevenue)</td>")
                [void]$sb.AppendLine("<td class=""num"">$(Format-Money $line.BillNow)</td>")
                [void]$sb.AppendLine("<td class=""num"">$(Format-Money $line.JtdVsEarned)</td>")
                [void]$sb.AppendLine("<td class=""notes"">$(& $enc $lineFlags)</td>")
                [void]$sb.AppendLine('</tr>')
            }
            [void]$sb.AppendLine('</tbody></table></details></td></tr>')
        }
    }

    [void]$sb.AppendLine('</tbody></table>')
    [void]$sb.AppendLine('</section>')

    [void]$sb.AppendLine('<section class="rules">')
    [void]$sb.AppendLine('<h2>Decision rules</h2>')
    [void]$sb.AppendLine('<ul>')
    [void]$sb.AppendLine('<li><strong>Progress:</strong> candidate bill = <code>max(0, Earned Revenue - Previously Invoiced)</code>.</li>')
    [void]$sb.AppendLine('<li><strong>Time and expense:</strong> candidate bill = <code>max(0, JTD Spent at Billing - Previously Invoiced)</code>.</li>')
    [void]$sb.AppendLine('<li><strong>JTD spent above earned revenue:</strong> the project row shows the total in the Unbilled column and Notes. On T&amp;M lines the line detail also carries an unbilled-cost flag.</li>')
    [void]$sb.AppendLine('<li><strong>Fully invoiced lines</strong> (Previously Invoiced % at 100%) produce no bill and no unbilled-cost finding.</li>')
    [void]$sb.AppendLine('<li><strong>Over-billed amounts</strong> are recorded in the CSV data but are not findings in this process. Only under-billing and under-recognition drive the verdict.</li>')
    [void]$sb.AppendLine('<li><strong>BILL NOW</strong> wins when any line''s candidate bill exceeds the threshold; otherwise any flag means <strong>REVIEW</strong>; otherwise <strong>NO ACTION</strong>. Parse failures are <strong>ERROR</strong>.</li>')
    [void]$sb.AppendLine('<li>The threshold gates amount-based decisions only. Totals mismatches, duplicate documents, an already-queued current invoice, and parse errors are structural and always surface.</li>')
    [void]$sb.AppendLine('<li>Figures are read as reported by the document; Earned Revenue is never recomputed from its percentage.</li>')
    [void]$sb.AppendLine('</ul>')
    [void]$sb.AppendLine('</section>')

    [void]$sb.AppendLine('</body>')
    [void]$sb.AppendLine('</html>')
    return $sb.ToString()
}

function Export-PreBillReports {
    <#
    .SYNOPSIS
        Write summary.csv, detail.csv, report.html and the extracted text for audit.
    .DESCRIPTION
        Without -IncludeClean, NO ACTION projects are omitted from the CSVs and HTML so the
        monthly review focuses on exceptions. The console summary always lists every project.
        Returns the run directory path.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Projects,
        [Parameter(Mandatory)][string]$OutputPath,
        [Parameter(Mandatory)][string]$RunStamp,
        [switch]$IncludeClean,
        [string]$InputFolder,
        [decimal]$Threshold = 0.01
    )

    $runDir = Join-Path $OutputPath $RunStamp
    $extractedDir = Join-Path $runDir 'extracted'
    New-Item -ItemType Directory -Force -Path $extractedDir | Out-Null

    $sorted = @($Projects | Sort-Object `
        @{ Expression = { $script:ActionOrder[$_.Action] } }, `
        @{ Expression = { $_.ContractNumber } })

    $included = if ($IncludeClean) { $sorted } else { @($sorted | Where-Object { $_.Action -ne 'NO ACTION' }) }

    # summary.csv
    $summaryColumns = @(
        'Action', 'BillingScheduleId', 'ContractNumber', 'ContractName', 'LegalEntity', 'ToDate',
        'ContractValue', 'PreviouslyInvoiced', 'JtdSpentAtBilling', 'EarnedRevenue',
        'CurrentBillingInvoice', 'BillNowTotal', 'UnbilledProgress', 'UnbilledTandM',
        'OverBilledTotal', 'JtdVsEarnedTotal', 'Flags', 'SourceFile'
    )
    $summaryRows = @($included | ForEach-Object {
        [pscustomobject][ordered]@{
            Action                = $_.Action
            BillingScheduleId     = $_.BillingScheduleId
            ContractNumber        = $_.ContractNumber
            ContractName          = $_.ContractName
            LegalEntity           = $_.LegalEntity
            ToDate                = $_.ToDate
            ContractValue         = Format-CsvAmount $_.ContractValue
            PreviouslyInvoiced    = Format-CsvAmount $_.PreviouslyInvoiced
            JtdSpentAtBilling     = Format-CsvAmount $_.JtdSpentAtBilling
            EarnedRevenue         = Format-CsvAmount $_.EarnedRevenue
            CurrentBillingInvoice = Format-CsvAmount $_.CurrentBillingInvoice
            BillNowTotal          = Format-CsvAmount $_.BillNowTotal
            UnbilledProgress      = Format-CsvAmount $_.UnbilledProgress
            UnbilledTandM         = Format-CsvAmount $_.UnbilledTandM
            OverBilledTotal       = Format-CsvAmount $_.OverBilledTotal
            JtdVsEarnedTotal      = Format-CsvAmount $_.JtdVsEarnedTotal
            Flags                 = (@($_.Flags) -join '; ')
            SourceFile            = $_.SourceFile
        }
    })
    Write-CsvFile -Rows $summaryRows -Columns $summaryColumns -Path (Join-Path $runDir 'summary.csv')

    # detail.csv
    $detailColumns = @(
        'Action', 'ContractNumber', 'ContractName', 'TaskCode', 'Description', 'FeeType',
        'ContractValue', 'PreviouslyInvoiced', 'PreviouslyInvoicedPct', 'JtdSpentAtBilling',
        'EarnedRevenuePct', 'EarnedRevenue', 'PctToComplete', 'CurrentBillingInvoice',
        'BillNow', 'OverBilled', 'JtdVsEarned', 'Flags', 'SourceFile'
    )
    $detailRows = [System.Collections.Generic.List[object]]::new()
    foreach ($project in $included) {
        foreach ($line in @($project.Lines)) {
            $detailRows.Add([pscustomobject][ordered]@{
                Action                = $line.Action
                ContractNumber        = $project.ContractNumber
                ContractName          = $project.ContractName
                TaskCode              = $line.TaskCode
                Description           = $line.Description
                FeeType               = $line.FeeType
                ContractValue         = Format-CsvAmount $line.ContractValue
                PreviouslyInvoiced    = Format-CsvAmount $line.PreviouslyInvoiced
                PreviouslyInvoicedPct = Format-Percent $line.PreviouslyInvoicedPct
                JtdSpentAtBilling     = Format-CsvAmount $line.JtdSpentAtBilling
                EarnedRevenuePct      = Format-Percent $line.EarnedRevenuePct
                EarnedRevenue         = Format-CsvAmount $line.EarnedRevenue
                PctToComplete         = Format-Percent $line.PctToComplete
                CurrentBillingInvoice = Format-CsvAmount $line.CurrentBillingInvoice
                BillNow               = Format-CsvAmount $line.BillNow
                OverBilled            = Format-CsvAmount $line.OverBilled
                JtdVsEarned           = Format-CsvAmount $line.JtdVsEarned
                Flags                 = (@($line.Flags) -join '; ')
                SourceFile            = $project.SourceFile
            })
        }
    }
    Write-CsvFile -Rows @($detailRows) -Columns $detailColumns -Path (Join-Path $runDir 'detail.csv')

    # extracted text for audit/debugging
    foreach ($project in $sorted) {
        if ([string]::IsNullOrWhiteSpace($project.SourceFile)) { continue }
        $base = [System.IO.Path]::GetFileNameWithoutExtension($project.SourceFile)
        $target = Join-Path $extractedDir ($base + '.txt')
        [System.IO.File]::WriteAllText($target, [string]$project.RawText, [System.Text.UTF8Encoding]::new($false))
    }

    # report.html
    $metadata = @{
        GeneratedAt     = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        InputFolder     = $InputFolder
        Threshold       = (Format-Money $Threshold)
        RunStamp        = $RunStamp
        TotalDocuments  = @($Projects).Count
        ParsedDocuments = @($Projects | Where-Object { $_.Action -ne 'ERROR' }).Count
    }
    $html = New-PreBillHtml -Projects @($included) -Metadata $metadata
    [System.IO.File]::WriteAllText((Join-Path $runDir 'report.html'), $html, [System.Text.UTF8Encoding]::new($false))

    return $runDir
}

Export-ModuleMember -Function `
    Resolve-PdfToTextPath, Get-PreBillRawText, ConvertFrom-PreBillText,
    ConvertFrom-Money, ConvertFrom-Percent, Measure-PreBill, Import-PreBills,
    Write-PreBillConsole, Export-PreBillReports, New-PreBillHtml
