$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

foreach ($service in @(@{ Name = 'voice-service'; Port = 8765 }, @{ Name = 'voice-input'; Port = 8766 })) {
    $listener = Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { Write-Host "$($service.Name) already stopped."; continue }
    try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($service.Port)/health" -TimeoutSec 2 } catch { $health = $null }
    $isJarvis = ($service.Port -eq 8765 -and $health.service -eq 'jarvis_voice') -or ($service.Port -eq 8766 -and $health.service -eq 'jarvis_voice_input')
    if (-not $isJarvis) { Write-Warning "Skipped PID $($listener.OwningProcess) on port $($service.Port): identity is not JARVIS."; continue }
    Stop-Process -Id $listener.OwningProcess -ErrorAction Stop
    Write-Host "$($service.Name) stopped."
}
