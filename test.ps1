param([switch]$Live)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$cargo = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'

Push-Location $projectRoot
try {
    & $cargo fmt -- --check
    if ($LASTEXITCODE -ne 0) { throw 'Rust formatting check failed.' }
    & $cargo test --all
    if ($LASTEXITCODE -ne 0) { throw 'Rust tests failed.' }
    & $python -m unittest discover -s voice_input_service -p 'test_*.py' -v
    if ($LASTEXITCODE -ne 0) { throw 'Voice-input tests failed.' }
    & $python -m unittest discover -s voice_service -p 'test_*.py' -v
    if ($LASTEXITCODE -ne 0) { throw 'Voice-service tests failed.' }
    & $python -m py_compile voice_input_service\app.py voice_service\app.py
    if ($Live) {
        Invoke-RestMethod http://127.0.0.1:8765/health -TimeoutSec 3 | Out-Null
        Invoke-RestMethod http://127.0.0.1:8766/health -TimeoutSec 3 | Out-Null
    }
    Write-Host 'JARVIS build and tests are healthy.'
} finally { Pop-Location }
