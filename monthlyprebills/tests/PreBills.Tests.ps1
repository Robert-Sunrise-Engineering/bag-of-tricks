# Pester 3.4-compatible tests (3.4.0 is the only version installed on the reference machine).
# All fixtures and expected values are wholly invented: the project names, contract numbers,
# descriptions, and amounts exist only to exercise the parser and the billing rules. Do not
# copy descriptions, contract identifiers, or figures from real pre-bill documents here.

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$modulePath = Join-Path (Split-Path -Parent $here) 'PreBills.psm1'
Import-Module $modulePath -Force

function Get-FixtureText {
    param([Parameter(Mandatory)][string]$Name)
    return Get-Content -LiteralPath (Join-Path $here "fixtures\$Name.txt") -Raw -Encoding UTF8
}

function Get-FixtureParsed {
    param([Parameter(Mandatory)][string]$Name)
    return ConvertFrom-PreBillText -Text (Get-FixtureText -Name $Name) -SourceFile "$Name.txt"
}

function New-MinimalPreBillText {
    param(
        [string]$RowContract = '1,000.00',
        [string]$RowPrev = '250.00',
        [string]$RowJtd = '200.00',
        [string]$RowEarned = '250.00',
        [string]$RowCurrent = '0.00',
        [string]$PrevPct = '25.00',
        [string]$TotalContract,
        [string]$TotalPrev,
        [string]$TotalJtd,
        [string]$TotalEarned,
        [string]$TotalCurrent = '0.00',
        [int]$ContinuationLines = 0,
        [switch]$AddBrokenRow,
        [switch]$BlankContractNumber,
        [switch]$AddPageHeader,
        [switch]$SpacedHyphenContinuation,
        [switch]$EmptyDescription,
        [switch]$TimeAndExpense
    )

    if (-not $PSBoundParameters.ContainsKey('TotalContract')) { $TotalContract = $RowContract }
    if (-not $PSBoundParameters.ContainsKey('TotalPrev')) { $TotalPrev = $RowPrev }
    if (-not $PSBoundParameters.ContainsKey('TotalJtd')) { $TotalJtd = $RowJtd }
    if (-not $PSBoundParameters.ContainsKey('TotalEarned')) { $TotalEarned = $RowEarned }

    $contractNumber = if ($BlankContractNumber) { '' } else { 'T0001' }
    $descCell = if ($EmptyDescription) { '                    ' } else { 'Test            ' }
    $feeType = if ($TimeAndExpense) { 'Time and expense' } else { 'Progress' }
    $dataRow = "T0001.001.001 | $descCell $feeType          $RowContract     $RowPrev      $PrevPct%        $RowJtd      25.00%    $RowEarned    25.00%    $RowCurrent"
    $totalRow = "                    $TotalContract     $TotalPrev      $TotalJtd      $TotalEarned      $TotalCurrent"

    $lines = @(
        'Pre-billing summary',
        '',
        'Billing schedule Id   :BSCHD000000001      Legal entity  :Test Entity',
        '',
        "Contract number       :$contractNumber               From date     :",
        '',
        'Contract name         :Test Project        To date       :10/31/2026',
        '',
        'Progress summary',
        '',
        $dataRow,
        ''
    )
    for ($n = 1; $n -le $ContinuationLines; $n++) {
        $lines += "continuation $n"
        $lines += ''
    }
    if ($SpacedHyphenContinuation) {
        $lines += 'Widget Support - $137 Prorated Apr 1 -'
        $lines += ''
        $lines += 'Sep 30, 2031'
        $lines += ''
    }
    if ($EmptyDescription) {
        $lines += 'Continued Description'
        $lines += ''
    }
    if ($AddPageHeader) {
        $lines += 'Pre-billing summary'
        $lines += ''
    }
    if ($AddBrokenRow) {
        $lines += 'T0001.001.002 | Broken row'
        $lines += ''
    }
    $lines += $totalRow
    return ($lines -join "`n")
}

Describe 'ConvertFrom-Money' {
    It 'parses a value with thousands separators' {
        ConvertFrom-Money '1,234.56' | Should Be ([decimal]1234.56)
    }
    It 'parses parentheses as a negative value' {
        ConvertFrom-Money '(123.45)' | Should Be ([decimal]-123.45)
    }
    It 'treats blank as zero' {
        ConvertFrom-Money '' | Should Be ([decimal]0)
    }
    It 'treats whitespace as zero' {
        ConvertFrom-Money '   ' | Should Be ([decimal]0)
    }
    It 'parses a negative sign' {
        ConvertFrom-Money '-5.00' | Should Be ([decimal]-5.00)
    }
    It 'throws on non-numeric text' {
        # Pester 3.4's "Should Throw" is unreliable on PowerShell 7, so assert explicitly.
        $threw = $false
        try { ConvertFrom-Money 'abc' | Out-Null } catch { $threw = $true }
        $threw | Should Be $true
    }
}

Describe 'ConvertFrom-Percent' {
    It 'parses a percentage' {
        ConvertFrom-Percent '100.00%' | Should Be ([decimal]100.00)
    }
    It 'parses zero' {
        ConvertFrom-Percent '0.00%' | Should Be ([decimal]0)
    }
    It 'treats blank as zero' {
        ConvertFrom-Percent '' | Should Be ([decimal]0)
    }
}

Describe 'Header parsing' {
    It 'reads the Alpha Ridge header' {
        $parsed = Get-FixtureParsed 'alpha-ridge'
        $parsed.Header.BillingScheduleId | Should Be 'BSCHD000000101'
        $parsed.Header.ContractNumber | Should Be 'T10001'
        $parsed.Header.ContractName | Should Be 'Alpha Ridge Cloud Services'
        $parsed.Header.LegalEntity | Should Be 'Example Engineering LLC'
        $parsed.Header.ToDate | Should Be '10/31/2026'
    }
    It 'stops the contract name before To date even with exactly two spaces' {
        # The Alpha Ridge header line has exactly two spaces before "To date" and the
        # name wraps onto the following indented line.
        $parsed = Get-FixtureParsed 'alpha-ridge'
        $parsed.Header.ContractName | Should Be 'Alpha Ridge Cloud Services'
        $parsed.Header.ToDate | Should Be '10/31/2026'
    }
    It 'accepts a blank From date without a parse error' {
        $parsed = Get-FixtureParsed 'alpha-ridge'
        ([string]$parsed.Header.FromDate) | Should Be ''
        @($parsed.ParseErrors).Count | Should Be 0
    }
    It 'flags a missing required header field as a parse error' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -BlankContractNumber) -SourceFile 'synthetic.pdf'
        (@($parsed.ParseErrors) -join '; ') | Should Match 'Missing required header field: Contract number'
        (Measure-PreBill -Parsed $parsed).Action | Should Be 'ERROR'
    }
}

Describe 'Row parsing' {
    It 'parses exactly four data rows for Alpha Ridge' {
        $parsed = Get-FixtureParsed 'alpha-ridge'
        @($parsed.Lines).Count | Should Be 4
    }
    It 'does not treat a dotted/underscored group row as a data row' {
        $parsed = Get-FixtureParsed 'alpha-ridge'
        $codes = @($parsed.Lines | ForEach-Object { $_.TaskCode })
        ($codes -contains 'T10001_001') | Should Be $false
        ($codes -contains 'T10001_001.001.001') | Should Be $true
    }
    It 'parses the three-space Time and expense variant' {
        $parsed = Get-FixtureParsed 'alpha-ridge'
        $line = @($parsed.Lines | Where-Object { $_.TaskCode -eq 'T10001_001.002.001' })[0]
        $line.FeeType | Should Be 'Time and expense'
    }
    It 'parses Beta Harbor T&M figures as reported' {
        $parsed = Get-FixtureParsed 'beta-harbor'
        $line = @($parsed.Lines | Where-Object { $_.TaskCode -eq 'T20002.0002.006' })[0]
        $line.FeeType | Should Be 'Time and expense'
        $line.PreviouslyInvoiced | Should Be ([decimal]862.40)
        $line.JtdSpentAtBilling | Should Be ([decimal]1347.90)
        $line.EarnedRevenue | Should Be ([decimal]862.40)
    }
    It 'reconstructs a description split mid-word (Multi- + Tenant)' {
        $parsed = Get-FixtureParsed 'alpha-ridge'
        $line = @($parsed.Lines | Where-Object { $_.TaskCode -eq 'T10001_001.002.001' })[0]
        $line.Description | Should Be 'Multi-Tenant Widget Support'
    }
    It 'joins multi-line descriptions' {
        $parsed = Get-FixtureParsed 'beta-harbor'
        $line = @($parsed.Lines | Where-Object { $_.TaskCode -eq 'T20002.0003' })[0]
        $line.Description | Should Be '0003 Indigo Sample Widget Subscription Service'
    }
    It 'reads the document total row' {
        $parsed = Get-FixtureParsed 'alpha-ridge'
        $parsed.Totals.ContractValue | Should Be ([decimal]1311.57)
        $parsed.Totals.PreviouslyInvoiced | Should Be ([decimal]958.75)
        $parsed.Totals.JtdSpentAtBilling | Should Be ([decimal]402.79)
        $parsed.Totals.EarnedRevenue | Should Be ([decimal]723.24)
        $parsed.Totals.CurrentBillingInvoice | Should Be ([decimal]0)
    }
    It 'finds 12 data rows across all three synthetic fixtures' {
        $names = @('alpha-ridge', 'beta-harbor', 'gamma-field')
        $total = 0
        foreach ($name in $names) { $total += @((Get-FixtureParsed $name).Lines).Count }
        $total | Should Be 12
    }
    It 'records a ParseError when description continuations exceed the cap' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -ContinuationLines 6) -SourceFile 'synthetic.pdf'
        (@($parsed.ParseErrors) -join '; ') | Should Match 'exceeds 5 lines'
        (Measure-PreBill -Parsed $parsed).Action | Should Be 'ERROR'
    }
    It 'records a ParseError for an unparseable dotted task row' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -AddBrokenRow) -SourceFile 'synthetic.pdf'
        (@($parsed.ParseErrors) -join '; ') | Should Match 'task row could not be parsed'
        (Measure-PreBill -Parsed $parsed).Action | Should Be 'ERROR'
    }
    It 'does not append a repeated page header to a description' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -AddPageHeader) -SourceFile 'synthetic.pdf'
        @($parsed.Lines)[0].Description | Should Be 'Test'
        @($parsed.ParseErrors).Count | Should Be 0
    }
    It 'keeps a space after a spaced dash continuation' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -SpacedHyphenContinuation) -SourceFile 'synthetic.pdf'
        @($parsed.Lines)[0].Description | Should Be 'Test Widget Support - $137 Prorated Apr 1 - Sep 30, 2031'
    }
    It 'does not add a leading space when the description cell is empty' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -EmptyDescription) -SourceFile 'synthetic.pdf'
        @($parsed.Lines)[0].Description | Should Be 'Continued Description'
    }
}

Describe 'Decision rules' {
    It 'marks a clean project NO ACTION' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText) -SourceFile 'synthetic.pdf'
        (Measure-PreBill -Parsed $parsed).Action | Should Be 'NO ACTION'
    }
    It 'marks Gamma Field NO ACTION' {
        (Measure-PreBill -Parsed (Get-FixtureParsed 'gamma-field')).Action | Should Be 'NO ACTION'
    }
    It 'bills Beta Harbor $485.50' {
        $project = Measure-PreBill -Parsed (Get-FixtureParsed 'beta-harbor')
        $project.Action | Should Be 'BILL NOW'
        $project.BillNowTotal | Should Be ([decimal]485.50)
        $project.OverBilledTotal | Should Be ([decimal]113.85)
    }
    It 'reports an under-billed progress line as BILL NOW' {
        # Synthetic: progress earned 300, previously invoiced 100 -> bill 200.
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -RowPrev '100.00' -RowEarned '300.00') -SourceFile 'synthetic.pdf'
        $project = Measure-PreBill -Parsed $parsed
        $project.BillNowTotal | Should Be ([decimal]200.00)
        $project.Action | Should Be 'BILL NOW'
    }
    It 'bills T&M when JTD spent exceeds previously invoiced' {
        $project = Measure-PreBill -Parsed (Get-FixtureParsed 'beta-harbor')
        @($project.Lines | Where-Object { $_.TaskCode -eq 'T20002.0002.006' })[0].BillNow | Should Be ([decimal]485.50)
    }
    It 'records over-billed progress as data, not a finding' {
        $project = Measure-PreBill -Parsed (Get-FixtureParsed 'alpha-ridge')
        $project.Action | Should Be 'NO ACTION'
        $project.OverBilledTotal | Should Be ([decimal]235.51)
        (@($project.Flags) -join '; ') | Should Not Match 'Over-billed'
    }
    It 'records the expected over-billed amounts as data' {
        $expected = @{
            'alpha-ridge' = @{ Over = [decimal]235.51; Action = 'NO ACTION' }
            'beta-harbor' = @{ Over = [decimal]113.85; Action = 'BILL NOW' }
        }
        foreach ($name in $expected.Keys) {
            $project = Measure-PreBill -Parsed (Get-FixtureParsed $name)
            $project.OverBilledTotal | Should Be $expected[$name].Over
            $project.Action | Should Be $expected[$name].Action
        }
    }
    It 'records 349.36 over-billed across the synthetic batch as data' {
        $names = @('alpha-ridge', 'beta-harbor', 'gamma-field')
        $over = [decimal]0
        foreach ($name in $names) { $over += (Measure-PreBill -Parsed (Get-FixtureParsed $name)).OverBilledTotal }
        $over | Should Be ([decimal]349.36)
    }
    It 'does not flag a progress line whose JTD exceeds contract value' {
        # JTD equals earned here, so this isolates the removed JTD/contract flag.
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -RowPrev '1200.00' -RowJtd '1200.00' -RowEarned '1200.00') -SourceFile 'synthetic.pdf'
        $project = Measure-PreBill -Parsed $parsed
        $project.BillNowTotal | Should Be ([decimal]0)
        @($project.Flags).Count | Should Be 0
        $project.Action | Should Be 'NO ACTION'
    }
    It 'flags T&M cost above earned revenue as under-recognition' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -TimeAndExpense -RowPrev '500.00' -RowJtd '300.00' -RowEarned '200.00') -SourceFile 'synthetic.pdf'
        $project = Measure-PreBill -Parsed $parsed
        $project.BillNowTotal | Should Be ([decimal]0)
        $project.Action | Should Be 'REVIEW'
        (@($project.Flags) -join '; ') | Should Match 'JTD spent exceeds earned revenue by \$100.00'
        (@(@($project.Lines)[0].Flags) -join '; ') | Should Match 'Unbilled cost \$100.00'
    }
    It 'flags progress cost above earned revenue as under-recognition' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -RowPrev '500.00' -RowJtd '700.00' -RowEarned '500.00') -SourceFile 'synthetic.pdf'
        $project = Measure-PreBill -Parsed $parsed
        $project.BillNowTotal | Should Be ([decimal]0)
        $project.Action | Should Be 'REVIEW'
        (@($project.Flags) -join '; ') | Should Match 'JTD spent exceeds earned revenue by \$200.00'
    }
    It 'does not bill a progress line that is 100% previously invoiced' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -RowPrev '100.00' -RowEarned '300.00' -PrevPct '100.00') -SourceFile 'synthetic.pdf'
        $project = Measure-PreBill -Parsed $parsed
        $project.BillNowTotal | Should Be ([decimal]0)
        @($project.Flags).Count | Should Be 0
        $project.Action | Should Be 'NO ACTION'
    }
    It 'does not flag unbilled cost on a T&M line that is 100% previously invoiced' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -TimeAndExpense -RowPrev '100.00' -RowJtd '300.00' -RowEarned '200.00' -PrevPct '100.00') -SourceFile 'synthetic.pdf'
        $project = Measure-PreBill -Parsed $parsed
        $project.BillNowTotal | Should Be ([decimal]0)
        $project.JtdVsEarnedTotal | Should Be ([decimal]0)
        @($project.Flags).Count | Should Be 0
        $project.Action | Should Be 'NO ACTION'
    }
    It 'honours -Threshold 200 for amount-based decisions' {
        $alpha = Measure-PreBill -Parsed (Get-FixtureParsed 'alpha-ridge') -Threshold 200
        $alpha.Action | Should Be 'NO ACTION'
        $alpha.OverBilledTotal | Should Be ([decimal]235.51)

        $beta = Measure-PreBill -Parsed (Get-FixtureParsed 'beta-harbor') -Threshold 200
        $beta.Action | Should Be 'BILL NOW'

        $gamma = Measure-PreBill -Parsed (Get-FixtureParsed 'gamma-field') -Threshold 200
        $gamma.Action | Should Be 'NO ACTION'
    }
    It 'raises a structural totals mismatch even at a high threshold' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -TotalEarned '300.00') -SourceFile 'synthetic.pdf'
        $project = Measure-PreBill -Parsed $parsed -Threshold 1000
        $project.Action | Should Be 'REVIEW'
        (@($project.Flags) -join '; ') | Should Match 'Totals mismatch: EarnedRevenue'
    }
    It 'raises an already-queued current invoice even at a high threshold' {
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -RowCurrent '25.00' -TotalCurrent '25.00') -SourceFile 'synthetic.pdf'
        $project = Measure-PreBill -Parsed $parsed -Threshold 1000
        $project.Action | Should Be 'REVIEW'
        (@($project.Flags) -join '; ') | Should Match 'Current billing invoice \$25.00 already queued'
    }
    It 'still reports BILL NOW when a current invoice is already queued' {
        # The queued-invoice flag surfaces, but the pre-test hierarchy gives BILL NOW
        # precedence when a candidate bill is above the threshold.
        $parsed = ConvertFrom-PreBillText -Text (New-MinimalPreBillText -RowPrev '0.00' -RowEarned '300.00' -RowCurrent '25.00' -TotalPrev '0.00' -TotalEarned '300.00' -TotalCurrent '25.00') -SourceFile 'synthetic.pdf'
        $project = Measure-PreBill -Parsed $parsed
        $project.BillNowTotal | Should Be ([decimal]300.00)
        $project.Action | Should Be 'BILL NOW'
        (@($project.Flags) -join '; ') | Should Match 'Current billing invoice \$25.00 already queued'
    }
    It 'cross-checks every fixture total against the summed lines' {
        $names = @('alpha-ridge', 'beta-harbor', 'gamma-field')
        foreach ($name in $names) {
            $project = Measure-PreBill -Parsed (Get-FixtureParsed $name)
            (@($project.Flags) -join '; ') | Should Not Match 'Totals mismatch'
        }
    }
    It 'marks a document with no pre-billing marker as ERROR' {
        $parsed = ConvertFrom-PreBillText -Text 'not a pre-bill' -SourceFile 'bad.pdf'
        (@($parsed.ParseErrors) -join '; ') | Should Match 'image-only'
        (Measure-PreBill -Parsed $parsed).Action | Should Be 'ERROR'
    }
}
