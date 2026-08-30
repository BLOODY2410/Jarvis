$ErrorActionPreference = 'Stop'
$serviceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $serviceRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    py -3.12 -m venv (Join-Path $serviceRoot '.venv')
}

& $python -m pip install --upgrade pip
& $python -m pip install --requirement (Join-Path $serviceRoot 'requirements.txt')
Write-Host 'Voice Input Core installed. The wake model downloads automatically on first start.'
