$ErrorActionPreference = 'Stop'
$serviceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path (Split-Path -Parent $serviceRoot) '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Shared venv not found. Run D:\Jarvis\install.ps1 first.'
}

Set-Location -LiteralPath $serviceRoot
& $python -m uvicorn app:app --host 127.0.0.1 --port 8765
