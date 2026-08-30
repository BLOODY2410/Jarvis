$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$cargo = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'
$logRoot = Join-Path $projectRoot 'logs'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Run D:\Jarvis\install.ps1 once before starting JARVIS.'
}
if (-not (Test-Path -LiteralPath $cargo)) {
    throw 'Rust cargo.exe was not found under your user profile.'
}

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$env:PYTHONUTF8 = '1'
$startedPids = [System.Collections.Generic.List[int]]::new()

function Test-Health([string]$Uri) {
    try {
        $response = Invoke-RestMethod -Uri $Uri -TimeoutSec 2
        return $null -ne $response
    } catch {
        return $false
    }
}

function Start-LocalService([string]$Name, [string]$Directory, [int]$Port) {
    $healthUri = "http://127.0.0.1:$Port/health"
    if (Test-Health $healthUri) {
        Write-Host "$Name already ready on port $Port."
        return
    }
    $stdout = Join-Path $logRoot "$Name.log"
    $stderr = Join-Path $logRoot "$Name-error.log"
    $process = Start-Process -FilePath $python `
        -ArgumentList @('-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', "$Port") `
        -WorkingDirectory $Directory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if (Test-Health $healthUri) {
            $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
            $startedPids.Add([int]$listener.OwningProcess)
            Write-Host "$Name ready."
            return
        }
        if ($process.HasExited -and $attempt -gt 4) {
            throw "$Name stopped during startup. See $stderr"
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name did not become ready. See $stderr"
}

function Test-TtsSynthesis {
    $checkPath = Join-Path $logRoot 'startup-tts-check.wav'
    $body = @{ text = 'Готово.' } | ConvertTo-Json -Compress
    $response = Invoke-WebRequest `
        -Uri 'http://127.0.0.1:8765/synthesize' `
        -Method Post `
        -ContentType 'application/json; charset=utf-8' `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
        -OutFile $checkPath `
        -PassThru `
        -TimeoutSec 20
    if ((Get-Item -LiteralPath $checkPath).Length -le 44) {
        throw 'Voice service returned an empty WAV during startup verification.'
    }
    $provider = $response.Headers['X-Jarvis-Provider'] -join ''
    $fallback = $response.Headers['X-Jarvis-Fallback-From'] -join ''
    Write-Host "TTS verified: provider=$provider, fallback-from=$fallback."
}

try {
    Start-LocalService 'voice-service' (Join-Path $projectRoot 'voice_service') 8765
    Test-TtsSynthesis
    Start-LocalService 'voice-input' (Join-Path $projectRoot 'voice_input_service') 8766
    Write-Host 'JARVIS ready. Press Ctrl+Alt+J, wait for the greeting, then speak.'
    Set-Location -LiteralPath $projectRoot
    & $cargo run
} finally {
    foreach ($processId in $startedPids) {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
    }
}
