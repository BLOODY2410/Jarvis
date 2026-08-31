param(
    [Parameter(Mandatory = $true)]
    [string]$WavPath,
    [double]$Threshold = -1
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$resolvedPath = (Resolve-Path -LiteralPath $WavPath).Path
$body = @{ path = $resolvedPath }
if ($Threshold -ge 0) {
    $body.threshold = $Threshold
}

$result = Invoke-RestMethod `
    -Method Post `
    -Uri 'http://127.0.0.1:8766/diagnostic/wake-wav' `
    -ContentType 'application/json; charset=utf-8' `
    -Body ($body | ConvertTo-Json)

$result | ConvertTo-Json -Depth 8
