[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $AppArguments
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Preflight = Join-Path $ProjectRoot 'launcher_config.py'
$EntryPoint = Join-Path $ProjectRoot 'run_server.py'

Set-Location -LiteralPath $ProjectRoot

$CheckOnly = $AppArguments -contains '--check'
$ConfigOnly = $AppArguments -contains '--config'
if ($CheckOnly -and $ConfigOnly) {
    throw 'Use either --check or --config, not both.'
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "V02 Python environment not found: $Python. Create a new V02 .venv; this launcher does not fall back to another Python."
}

if ($CheckOnly -or $ConfigOnly) {
    $Mode = if ($CheckOnly) { '--check' } else { '--config' }
    & $Python $Preflight $Mode
    exit $LASTEXITCODE
}

& $Python $Preflight '--check'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Preserve the existing run_server.py CLI. Runtime args are forwarded verbatim.
& $Python $EntryPoint @AppArguments
exit $LASTEXITCODE
