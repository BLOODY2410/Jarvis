$ErrorActionPreference = 'Stop'
$serviceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $serviceRoot '.venv\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$code = @'
import sounddevice as sd

default = sd.default.device[0]
print("Input devices:")
for index, device in enumerate(sd.query_devices()):
    if device["max_input_channels"] > 0:
        marker = " DEFAULT" if index == default else ""
        print(
            "[{}]{} {} | inputs={} | default_rate={:.0f} Hz | hostapi={}".format(
                index,
                marker,
                device["name"],
                device["max_input_channels"],
                device["default_samplerate"],
                device["hostapi"],
            )
        )
print("Actual system default input index: {}".format(default))
'@
& $python -c $code
