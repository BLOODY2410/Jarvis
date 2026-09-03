$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    py -3.12 -m venv (Join-Path $projectRoot '.venv')
}

& $python -m pip install --upgrade pip uv
if ($LASTEXITCODE -ne 0) { throw 'Could not install pip/uv.' }
Push-Location $projectRoot
try {
    & $python -m uv lock
    if ($LASTEXITCODE -ne 0) { throw 'uv.lock generation failed.' }
    & $python -m uv sync --python $python --extra voice --extra windows --extra dev
    if ($LASTEXITCODE -ne 0) { throw 'JARVIS v2 dependency installation failed.' }
} finally { Pop-Location }
Write-Host 'JARVIS v2 installed. Legacy Rust remains available through .\start-legacy.ps1.'
