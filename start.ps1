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

function Test-Configuration {
    $envPath = Join-Path $projectRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) { throw 'Missing D:\Jarvis\.env. Copy .env.example and add your keys.' }
    $entries = @{}
    foreach ($line in Get-Content -LiteralPath $envPath) {
        if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)\s*$') { $entries[$matches[1]] = $matches[2] }
    }
    if (-not $entries.ContainsKey('GROQ_API_KEY') -or [string]::IsNullOrWhiteSpace($entries['GROQ_API_KEY']) -or $entries['GROQ_API_KEY'] -match 'replace_me') {
        throw 'GROQ_API_KEY is missing or still a placeholder in .env.'
    }
}

function Rotate-Log([string]$Path) {
    if ((Test-Path -LiteralPath $Path) -and (Get-Item -LiteralPath $Path).Length -gt 5MB) {
        $archive = "$Path.1"
        if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
        Move-Item -LiteralPath $Path -Destination $archive
    }
}

function Get-Health([string]$Uri) {
    try {
        return Invoke-RestMethod -Uri $Uri -TimeoutSec 2
    } catch {
        return $null
    }
}

function Test-ServiceIdentity([int]$Port, $Health) {
    if ($null -eq $Health) { return $false }
    if ($Port -eq 8765) { return $Health.service -eq 'jarvis_voice' }
    if ($Port -eq 8766) { return $Health.service -eq 'jarvis_voice_input' }
    return $false
}

function Start-LocalService([string]$Name, [string]$Directory, [int]$Port) {
    $healthUri = "http://127.0.0.1:$Port/health"
    $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $health = Get-Health $healthUri
    if ($existing -and -not (Test-ServiceIdentity $Port $health)) {
        throw "Port $Port is occupied by a process that is not the expected JARVIS service (PID $($existing.OwningProcess))."
    }
    if ($existing -and (Test-ServiceIdentity $Port $health)) {
        Write-Host "$Name already ready on port $Port."
        return
    }
    $stdout = Join-Path $logRoot "$Name.log"
    $stderr = Join-Path $logRoot "$Name-error.log"
    Rotate-Log $stdout
    Rotate-Log $stderr
    $process = Start-Process -FilePath $python `
        -ArgumentList @('-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', "$Port") `
        -WorkingDirectory $Directory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        $health = Get-Health $healthUri
        if (Test-ServiceIdentity $Port $health) {
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
    $body = @{ text = 'Jarvis startup check.' } | ConvertTo-Json -Compress
    $response = Invoke-WebRequest `
        -Uri 'http://127.0.0.1:8765/synthesize' `
        -Method Post `
        -UseBasicParsing `
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
    Test-Configuration
    Start-LocalService 'voice-service' (Join-Path $projectRoot 'voice_service') 8765
    Test-TtsSynthesis
    Start-LocalService 'voice-input' (Join-Path $projectRoot 'voice_input_service') 8766
    Write-Host 'JARVIS ready. Say "Джарвіс" or press Ctrl+Alt+J, wait for the short cue, then speak.'
    Set-Location -LiteralPath $projectRoot
    & $cargo run
} finally {
    foreach ($processId in $startedPids) {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
    }
}
