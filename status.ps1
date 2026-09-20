$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pidFile = Join-Path $projectRoot '.run\jarvis-v2.pid'

Push-Location $projectRoot
try {
    & $python -m jarvis_v2 --doctor
    if (Test-Path -LiteralPath $pidFile) {
        $jarvisPid = (Get-Content -Raw -LiteralPath $pidFile).Trim()
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $jarvisPid" -ErrorAction SilentlyContinue
        if ($process -and $process.CommandLine -match 'jarvis_v2') {
            Write-Host "runtime: running (PID $jarvisPid)"
        } else {
            Write-Host 'runtime: stale PID file'
        }
    } else {
        Write-Host 'runtime: stopped'
    }
} finally { Pop-Location }
