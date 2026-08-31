# JARVIS Voice Core

Локальний HTTP sidecar на `127.0.0.1:8765`. У режимі `auto` основним TTS є Fish Audio, а локальний український Piper `uk_UA-mykyta-high` автоматично підхоплює запит при 401/403/429/5xx, мережевому timeout або іншій помилці Fish. Старий контракт не змінився: `POST /synthesize` з `{"text":"..."}` повертає WAV, тому Rust Core не потребує окремої інтеграції.

## Конфігурація

Сервіс сам читає `D:\Jarvis\.env`. Значення ключів не виводяться у логи або `/health`.

```dotenv
JARVIS_TTS_PROVIDER=auto
FISH_AUDIO_API_KEY=replace_me
FISH_AUDIO_REFERENCE_ID=replace_with_voice_model_id
FISH_AUDIO_MODEL=s2.1-pro-free
FISH_AUDIO_FALLBACK_MODEL=s2-pro
FISH_AUDIO_LATENCY=low
FISH_AUDIO_SPEED=0.97
FISH_AUDIO_CONNECT_TIMEOUT_SECS=2.5
FISH_AUDIO_READ_TIMEOUT_SECS=12
FISH_AUDIO_RETRIES=1
FISH_AUDIO_FX_ENABLED=false
FISH_AUDIO_ALLOW_PAID_FALLBACK=false
JARVIS_TTS_CACHE_MAX_CHARS=120
```

`FISH_AUDIO_REFERENCE_ID` — ID голосової моделі Fish Audio. Також підтримується сумісна назва `FISH_AUDIO_VOICE_ID`. Якщо ключа або ID немає, `/health` покаже `missing_api_key` чи `missing_reference_id`, а `auto` без зупинки використовуватиме Piper.

`POST /synthesize/stream` віддає PCM chunks одразу після Fish, і Rust починає playback після мінімального буфера; сумісний `POST /synthesize` як і раніше повертає завершений WAV. `FISH_AUDIO_SPEED` окремо керує Fish streaming і WAV payload; допустимий діапазон 0.90–1.10, cinematic default — 0.97. Шість коротких outcome-підтверджень прогріваються у фоні, а `GET /ack/{name}` ніколи не робить live cloud call. Версія cache key не допускає відтворення старих фраз. Piper завантажується у фоні й безпечно очікується лише при fallback.

Перехід із `s2.1-pro-free` на платну модель заборонений за замовчуванням. Він можливий лише після явного `FISH_AUDIO_ALLOW_PAID_FALLBACK=true`; інакше будь-яке відхилення free-моделі переходить у Piper.

Застарілий Piper JARVIS FX за замовчуванням не накладається на Fish (`FISH_AUDIO_FX_ENABLED=false`). Для Piper він лишається доступним через `JARVIS_TTS_MODE=jarvis_reference` та `JARVIS_FX_ENABLED=true`.

## Запуск і перевірка

Одна команда встановлює залежності, запускає тести й сам Jarvis:

```powershell
cd D:\Jarvis
.\test-and-start.ps1
```

Після готовності відкрийте `http://127.0.0.1:8765/health`. Поля `active_provider`, `model`, `reference`, `last_fallback_reason` і `cache` не містять секретів. У відповіді `/synthesize` є безпечні заголовки `X-Jarvis-Provider`, `X-Jarvis-Model`, `X-Jarvis-Cache` і `X-Jarvis-Fallback-From`.

## A/B Fish проти Piper

Коли Voice Core запущений, у другому PowerShell:

```powershell
cd D:\Jarvis\voice_service
.\test_service.ps1 -Play
```

Скрипт створить українські пари `test_1_fish.wav` / `test_1_piper.wav` тощо. Якщо Fish недоступний, заголовок покаже фактичний `provider=piper`, тому результат не маскує fallback під Fish.

## Тести

```powershell
cd D:\Jarvis\voice_service
..\.venv\Scripts\python.exe -m unittest -v test_app.py
```

Наскрізний Rust → localhost TTS → playback після запуску sidecar:

```powershell
cd D:\Jarvis
cargo test voice_service_playback -- --ignored --nocapture
```

Для повністю офлайн-режиму встановіть `JARVIS_TTS_PROVIDER=piper`. Для явного A/B один запит може додати `"provider":"fish"` або `"provider":"piper"`; старий JSON лише з `text` працює без змін.
