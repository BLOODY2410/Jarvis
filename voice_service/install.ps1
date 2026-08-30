$ErrorActionPreference = 'Stop'
$serviceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $serviceRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    py -3.12 -m venv (Join-Path $serviceRoot '.venv')
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install --requirement (Join-Path $serviceRoot 'requirements.txt')

Write-Host 'Voice Core installed. Start it with: .\start.ps1'
