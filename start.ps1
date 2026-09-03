$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'JARVIS v2 is not installed. Run .\install.ps1 first.'
}

Push-Location $projectRoot
try {
    & $python -m jarvis_v2
    if ($LASTEXITCODE -ne 0) { throw "JARVIS v2 exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}
