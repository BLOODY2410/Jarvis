$ErrorActionPreference = 'SilentlyContinue'
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8766/stop' | Out-Null
Write-Host 'Emergency stop signal sent to Voice Input Core.'
