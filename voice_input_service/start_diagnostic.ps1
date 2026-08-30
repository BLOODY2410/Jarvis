param(
    [string]$Device = ''
)

$ErrorActionPreference = 'Stop'
$serviceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path (Split-Path -Parent $serviceRoot) '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Shared venv not found. Run D:\Jarvis\install.ps1 first.'
}

$activeListener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($activeListener) {
    throw "Port 8766 is already used by PID $($activeListener.OwningProcess). Stop the existing Voice Input Core window with Ctrl+C first."
}

$env:PYTHONUTF8 = '1'
$env:JARVIS_DIAGNOSTIC = 'true'
$env:JARVIS_INPUT_LOG_LEVEL = 'INFO'
if ($Device) {
    $env:JARVIS_MIC_DEVICE = $Device
}
if (-not (Test-Path Env:JARVIS_BARGE_IN_ENABLED)) {
    $env:JARVIS_BARGE_IN_ENABLED = 'false'
}

$diagnosticDir = Join-Path $serviceRoot 'diagnostics'
Write-Host "Diagnostic mode ON. WAV files: $diagnosticDir"
Write-Host "Barge-in: $env:JARVIS_BARGE_IN_ENABLED"
Set-Location -LiteralPath $serviceRoot
& $python -m uvicorn app:app --host 127.0.0.1 --port 8766
