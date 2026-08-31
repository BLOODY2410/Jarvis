$ErrorActionPreference = 'Stop'
$path = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'logs\latency.jsonl'
if (-not (Test-Path -LiteralPath $path)) { throw 'No latency samples yet. Run at least 20 voice commands first.' }
$samples = @(Get-Content -LiteralPath $path | ForEach-Object { try { $_ | ConvertFrom-Json } catch {} } | Where-Object { $_.path -eq 'fast_path' } | Select-Object -ExpandProperty speech_end_to_tool_start_ms)
if ($samples.Count -lt 1) { throw 'No fast-path latency samples found.' }
$sorted = @($samples | Sort-Object)
$median = $sorted[[math]::Floor($sorted.Count / 2)]
$p95Index = [math]::Min($sorted.Count - 1, [math]::Ceiling($sorted.Count * 0.95) - 1)
$p95 = $sorted[$p95Index]
Write-Host "Fast Path samples=$($sorted.Count) median=${median}ms p95=${p95}ms"
if ($sorted.Count -lt 20) { Write-Warning 'Acceptance requires at least 20 repetitions.' }
if ($median -gt 1200 -or $p95 -gt 1500) { exit 2 }
