$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$cargo = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'

if (-not (Test-Path -LiteralPath $cargo)) {
    throw 'Rust cargo.exe was not found. Install Rust first.'
}
if (-not (Test-Path -LiteralPath $python)) {
    & (Join-Path $projectRoot 'install.ps1')
}
& $python -c 'import fastapi, requests, dotenv, piper, pedalboard'
if ($LASTEXITCODE -ne 0) {
    & (Join-Path $projectRoot 'install.ps1')
}

Write-Host '1/4 Rust Fast Command Engine tests'
& $cargo test core::fast_command::tests --manifest-path (Join-Path $projectRoot 'Cargo.toml')
if ($LASTEXITCODE -ne 0) { throw 'Rust Fast Path tests failed.' }

Write-Host '2/4 Voice Core provider/cache/fallback tests'
Push-Location (Join-Path $projectRoot 'voice_service')
try {
    & $python -m unittest -v test_app.py
    if ($LASTEXITCODE -ne 0) { throw 'Voice Core tests failed.' }
} finally {
    Pop-Location
}

Write-Host '3/4 Voice Input adaptive VAD/STT tests'
Push-Location (Join-Path $projectRoot 'voice_input_service')
try {
    & $python -m unittest -v test_app.py
    if ($LASTEXITCODE -ne 0) { throw 'Voice Input tests failed.' }
} finally {
    Pop-Location
}

Write-Host '4/4 Tests passed. Starting JARVIS and verifying local service health.'
& (Join-Path $projectRoot 'start.ps1')
