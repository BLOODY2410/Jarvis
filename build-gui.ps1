$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
try {
    uv sync --extra voice --extra windows --extra gui
    if ($LASTEXITCODE -ne 0) { throw 'GUI build dependencies could not be installed.' }
    uv run --extra voice --extra windows --extra gui pyinstaller --noconfirm --clean --windowed --name JARVIS --paths . --additional-hooks-dir pyinstaller_hooks jarvis_v2\desktop.py
    if ($LASTEXITCODE -ne 0) { throw 'JARVIS.exe build failed.' }
} finally { Pop-Location }
Write-Host 'Created dist\JARVIS\JARVIS.exe. Keep the whole JARVIS folder together; its .env is stored beside the executable.'
