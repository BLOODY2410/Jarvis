$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

function Show-Service([string]$Name, [int]$Port) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { Write-Host "${Name}: stopped"; return }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($Port -eq 8765 -and $health.service -eq 'jarvis_voice') {
            Write-Host "${Name}: ready (PID $($listener.OwningProcess), provider=$($health.active_provider), piper-ready=$($health.piper.ready))"
        } elseif ($Port -eq 8766 -and $health.service -eq 'jarvis_voice_input') {
            Write-Host "${Name}: ready (PID $($listener.OwningProcess), state=$($health.state), frame=$($health.chunk_ms)ms)"
        } else { Write-Host "${Name}: port occupied by an unknown service (PID $($listener.OwningProcess))" }
    } catch { Write-Host "${Name}: port open but health check failed (PID $($listener.OwningProcess))" }
}

Show-Service 'voice-service' 8765
Show-Service 'voice-input' 8766

$envPath = Join-Path $PSScriptRoot '.env'
if (Test-Path -LiteralPath $envPath) {
    $settings = @{}
    foreach ($line in Get-Content -LiteralPath $envPath) {
        if ($line -match '^\s*([^#=]+)=(.*)$') { $settings[$matches[1].Trim()] = $matches[2].Trim() }
    }
    $configured = @()
    foreach ($provider in @(@('cerebras','CEREBRAS_API_KEY'), @('gemini','GEMINI_API_KEY'), @('groq','GROQ_API_KEY'), @('openrouter','OPENROUTER_API_KEY'), @('mistral','MISTRAL_API_KEY'))) {
        $value = $settings[$provider[1]]
        if ($value -and $value -notin @('replace_me','changeme')) { $configured += $provider[0] }
    }
    $pc = if ($settings['JARVIS_PC_BRAIN_PROVIDER']) { $settings['JARVIS_PC_BRAIN_PROVIDER'] } else { 'cerebras' }
    $chat = if ($settings['JARVIS_CHAT_BRAIN_PROVIDER']) { $settings['JARVIS_CHAT_BRAIN_PROVIDER'] } else { 'gemini' }
    $live = if ($settings['JARVIS_LIVE_BRAIN_PROVIDER']) { $settings['JARVIS_LIVE_BRAIN_PROVIDER'] } else { 'gemini' }
    Write-Host "AI Router: pc=$pc, chat=$chat, live=$live, configured=$($configured -join ',')"
}
