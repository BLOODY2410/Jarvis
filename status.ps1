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
