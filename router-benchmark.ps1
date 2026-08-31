param([switch]$LiveProviders)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
Set-Location -LiteralPath $PSScriptRoot
if ($LiveProviders) {
    Write-Host 'Live provider benchmark may consume configured API quota; no local tools will be executed.'
    cargo run --quiet -- --router-benchmark-live
} else {
    cargo run --quiet -- --router-benchmark
}
