$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
try {
    uv sync --extra voice --extra windows --extra dev
    if ($LASTEXITCODE -ne 0) { throw 'JARVIS dependency installation failed.' }
} finally { Pop-Location }
Write-Host 'JARVIS v2 installed. Start it with uv run jarvis.'
