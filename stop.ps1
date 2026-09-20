$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $projectRoot '.run\jarvis-v2.pid'

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host 'JARVIS v2 already stopped.'
    exit 0
}

$jarvisPid = 0
if (-not [int]::TryParse((Get-Content -Raw -LiteralPath $pidFile).Trim(), [ref]$jarvisPid)) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Warning 'Removed an invalid JARVIS v2 PID file.'
    exit 0
}

$process = Get-CimInstance Win32_Process -Filter "ProcessId = $jarvisPid" -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host 'Removed a stale JARVIS v2 PID file.'
    exit 0
}
if ($process.Name -notmatch '^python(?:w)?\.exe$' -or $process.CommandLine -notmatch 'jarvis_v2') {
    throw "PID $jarvisPid does not belong to JARVIS v2; refusing to stop it."
}

Stop-Process -Id $jarvisPid -ErrorAction Stop
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host 'JARVIS v2 stopped.'
