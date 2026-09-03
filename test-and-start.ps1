$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.venv\Scripts\python.exe'))) {
    & (Join-Path $projectRoot 'install.ps1')
}
Write-Host 'Running JARVIS v2, voice, and preserved legacy tests.'
& (Join-Path $projectRoot 'test.ps1')
if ($LASTEXITCODE -ne 0) { throw 'JARVIS tests failed.' }
Write-Host 'Tests passed. Starting JARVIS v2.'
& (Join-Path $projectRoot 'start.ps1')
