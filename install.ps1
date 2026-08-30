$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    py -3.12 -m venv (Join-Path $projectRoot '.venv')
}

& $python -m pip install --upgrade pip
& $python -m pip install --requirement (Join-Path $projectRoot 'voice_service\requirements.txt')
& $python -m pip install --requirement (Join-Path $projectRoot 'voice_input_service\requirements.txt')
Write-Host 'JARVIS installed into D:\Jarvis\.venv.'
