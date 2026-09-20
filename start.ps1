$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
try {
    uv sync --extra voice --extra windows
    if ($LASTEXITCODE -ne 0) { throw 'Dependency sync failed.' }
    uv run jarvis
    if ($LASTEXITCODE -ne 0) { throw "JARVIS exited with code $LASTEXITCODE." }
} finally { Pop-Location }
