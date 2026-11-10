#Requires -Version 7.0
<#
.SYNOPSIS
    Review a folder of monthly Deltek/Vantagepoint pre-billing summary PDFs.

.DESCRIPTION
    Extracts every PDF in the input folder with pdftotext, applies the per-line billing
    rules, prints an exception-focused console summary, and writes summary.csv, detail.csv,
    report.html and the extracted text under reports/<yyyy-MM-dd-HHmm>/.

    Defaults come from config.json, then config.local.json (machine overrides), then any
    parameters supplied on the command line.

.PARAMETER InputPath
    Folder containing the monthly pre-bill PDFs. Default: ./newprebills

.PARAMETER OutputPath
    Folder that receives the per-run report directory. Default: ./reports

.PARAMETER Threshold
    Amount above which a candidate bill or a variance raises a decision. Default: 0.01.
    Structural problems (totals mismatch, duplicate documents, parse errors, an already
    queued current invoice) surface regardless of this value.

.PARAMETER PdfToTextPath
    Explicit path to pdftotext.exe. By default the tool checks PATH and the Git for
    Windows ucrt64/usr bin directories.

.PARAMETER IncludeClean
    Include NO ACTION projects in the CSVs and HTML report. The console always lists
    every project. Default: off (exceptions only).

.PARAMETER Open
    Open report.html in the default browser after the run.

.PARAMETER PassThru
    Emit the project objects to the pipeline for ad-hoc filtering.

.PARAMETER FailOnBillNow
    Exit with code 2 when at least one project is BILL NOW.

.EXAMPLE
    .\Get-PreBills.ps1

.EXAMPLE
    .\Get-PreBills.ps1 -InputPath .\newprebills -Threshold 200 -Open

.EXAMPLE
    .\Get-PreBills.ps1 -IncludeClean -PassThru | Where-Object Action -eq 'BILL NOW'
#>
[CmdletBinding()]
param(
    [string]$InputPath,
    [string]$OutputPath,
    [decimal]$Threshold,
    [string]$PdfToTextPath,
    [switch]$IncludeClean,
    [switch]$Open,
    [switch]$PassThru,
    [switch]$FailOnBillNow
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$modulePath = Join-Path $scriptDir 'PreBills.psm1'
Import-Module $modulePath -Force

# --- Configuration ------------------------------------------------------------
# Built-in defaults, overridden by config.json then config.local.json, then CLI parameters.
$config = [ordered]@{
    inputPath     = 'newprebills'
    outputPath    = 'reports'
    threshold     = 0.01
    pdfToTextPath = ''
}

foreach ($configName in @('config.json', 'config.local.json')) {
    $configPath = Join-Path $scriptDir $configName
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { continue }
    try {
        $override = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    }
    catch {
        Write-Host "Could not read ${configName}: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
    foreach ($property in $override.PSObject.Properties) {
        if ($null -eq $property.Value) { continue }
        if ([string]::IsNullOrWhiteSpace([string]$property.Value)) { continue }
        $config[$property.Name] = $property.Value
    }
}

function Resolve-ConfigPath {
    param([string]$BaseDirectory, [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return [System.IO.Path]::GetFullPath($Path) }
    return [System.IO.Path]::GetFullPath((Join-Path $BaseDirectory $Path))
}

$resolvedInput = if ($PSBoundParameters.ContainsKey('InputPath')) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($InputPath)
}
else {
    Resolve-ConfigPath -BaseDirectory $scriptDir -Path ([string]$config['inputPath'])
}

$resolvedOutput = if ($PSBoundParameters.ContainsKey('OutputPath')) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
}
else {
    Resolve-ConfigPath -BaseDirectory $scriptDir -Path ([string]$config['outputPath'])
}

$resolvedThreshold = if ($PSBoundParameters.ContainsKey('Threshold')) { $Threshold } else { [decimal]$config['threshold'] }

$configuredPdfToText = [string]$config['pdfToTextPath']
$resolvedPdfToText = if ($PSBoundParameters.ContainsKey('PdfToTextPath')) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PdfToTextPath)
}
elseif (-not [string]::IsNullOrWhiteSpace($configuredPdfToText)) {
    Resolve-ConfigPath -BaseDirectory $scriptDir -Path $configuredPdfToText
}
else {
    $null
}

# --- Fatal preconditions ------------------------------------------------------

try {
    $pdftotext = Resolve-PdfToTextPath -Override $resolvedPdfToText
}
catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

if (-not (Test-Path -LiteralPath $resolvedInput -PathType Container)) {
    Write-Host "Input folder not found: $resolvedInput" -ForegroundColor Red
    exit 1
}

$pdfCount = @(Get-ChildItem -LiteralPath $resolvedInput -Filter '*.pdf' -File).Count
if ($pdfCount -eq 0) {
    Write-Host "No PDF files found in $resolvedInput" -ForegroundColor Red
    exit 1
}

# --- Run ----------------------------------------------------------------------

$projects = @(Import-PreBills -InputPath $resolvedInput -PdfToTextPath $pdftotext -Threshold $resolvedThreshold)

if ($projects.Count -eq 0) {
    Write-Host "No pre-bill documents were processed from $resolvedInput" -ForegroundColor Red
    exit 1
}

$runStamp = Get-Date -Format 'yyyy-MM-dd-HHmm'
Write-PreBillConsole -Projects $projects

$runDirectory = Export-PreBillReports -Projects $projects -OutputPath $resolvedOutput `
    -RunStamp $runStamp -IncludeClean:$IncludeClean -InputFolder $resolvedInput `
    -Threshold $resolvedThreshold

Write-Host "Reports written to $runDirectory" -ForegroundColor Cyan

if ($Open) {
    $reportPath = Join-Path $runDirectory 'report.html'
    Start-Process -FilePath $reportPath
}

if ($PassThru) { $projects }

# --- Exit codes ---------------------------------------------------------------

$anyBillNow = @($projects | Where-Object { $_.Action -eq 'BILL NOW' }).Count -gt 0
$allFailed = @($projects | Where-Object { $_.Action -ne 'ERROR' }).Count -eq 0

if ($allFailed) { exit 1 }
if ($FailOnBillNow -and $anyBillNow) { exit 2 }
exit 0
