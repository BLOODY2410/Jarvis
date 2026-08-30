$ErrorActionPreference = 'Stop'
$serviceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $serviceRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Venv not found. Run install.ps1 first.'
}

$activeListener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($activeListener) {
    throw "Port 8766 is already used by PID $($activeListener.OwningProcess). Stop the existing Voice Input Core window with Ctrl+C first."
}

Set-Location -LiteralPath $serviceRoot
& $python -m uvicorn app:app --host 127.0.0.1 --port 8766
