$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
try {
    uv run --extra dev pytest tests_v2
    if ($LASTEXITCODE -ne 0) { throw 'JARVIS tests failed.' }
    uv run --extra dev ruff check jarvis_v2 tests_v2
    if ($LASTEXITCODE -ne 0) { throw 'JARVIS lint failed.' }
    $files = Get-ChildItem jarvis_v2 -Filter '*.py' | ForEach-Object FullName
    uv run python -m py_compile $files
    if ($LASTEXITCODE -ne 0) { throw 'JARVIS compilation failed.' }
} finally { Pop-Location }
