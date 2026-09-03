$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
Push-Location $projectRoot
try {
    & $python -m jarvis_v2 --benchmark
    if ($LASTEXITCODE -ne 0) { throw 'JARVIS v2 benchmark failed.' }
} finally { Pop-Location }
