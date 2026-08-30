param(
    [switch]$Play,
    [string]$BaseUrl = 'http://127.0.0.1:8765'
)

$ErrorActionPreference = 'Stop'
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
    foreach ($mode in @('raw', 'jarvis_reference')) {
        $output = Join-Path $PSScriptRoot ("test_{0}_{1}.wav" -f ($index + 1), $mode)
        $body = @{ text = $phrases[$index]; mode = $mode } | ConvertTo-Json -Compress
        $response = Invoke-WebRequest `
            -Uri "$BaseUrl/synthesize" `
            -Method Post `
            -ContentType 'application/json; charset=utf-8' `
            -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
            -OutFile $output `
            -PassThru

        $wav = Get-Item -LiteralPath $output
        if ($wav.Length -le 44) {
            throw "The service returned an empty WAV file for mode '$mode'."
        }
        $responseMode = $response.Headers['X-Jarvis-Mode'] -join ''
        Write-Host ("Created {0}: {1} bytes, mode={2}" -f $wav.Name, $wav.Length, $responseMode)
        $pair += $output
    }
    if ($Play) {
        foreach ($output in $pair) {
            Write-Host ("Playing {0}" -f (Split-Path -Leaf $output))
            (New-Object Media.SoundPlayer $output).PlaySync()
        }
    }
}
Write-Host 'A/B files are ready. Compare test_1_raw.wav with test_1_jarvis_reference.wav first.'
