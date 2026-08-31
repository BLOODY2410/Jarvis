param(
    [switch]$Play,
    [string]$BaseUrl = 'http://127.0.0.1:8765'
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$health = Invoke-RestMethod -Uri "$BaseUrl/health"
$health | ConvertTo-Json -Depth 4

$phrases = @(
    "Вітаю. Усі системи працюють у штатному режимі.",
    "Джарвіс готовий до роботи. Чим я можу допомогти?",
    "Температура процесора — сорок два градуси. Мережеве з'єднання стабільне.",
    "Завдання виконано. Очікую на ваші подальші вказівки."
)

foreach ($index in 0..($phrases.Count - 1)) {
    $pair = @()
    foreach ($provider in @('fish', 'piper')) {
        $output = Join-Path $PSScriptRoot ("test_{0}_{1}.wav" -f ($index + 1), $provider)
        $body = @{ text = $phrases[$index]; provider = $provider } | ConvertTo-Json -Compress
        $response = Invoke-WebRequest `
            -Uri "$BaseUrl/synthesize" `
            -Method Post `
            -UseBasicParsing `
            -ContentType 'application/json; charset=utf-8' `
            -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
            -OutFile $output `
            -PassThru

        $wav = Get-Item -LiteralPath $output
        if ($wav.Length -le 44) {
            throw "The service returned an empty WAV file for provider '$provider'."
        }
        $actualProvider = $response.Headers['X-Jarvis-Provider'] -join ''
        $cache = $response.Headers['X-Jarvis-Cache'] -join ''
        Write-Host ("Created {0}: {1} bytes, provider={2}, cache={3}" -f $wav.Name, $wav.Length, $actualProvider, $cache)
        $pair += $output
    }
    if ($Play) {
        foreach ($output in $pair) {
            Write-Host ("Playing {0}" -f (Split-Path -Leaf $output))
            (New-Object Media.SoundPlayer $output).PlaySync()
        }
    }
}
Write-Host 'A/B files are ready. Compare test_1_fish.wav with test_1_piper.wav first.'
