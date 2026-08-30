# JARVIS Voice Core

Локальний HTTP sidecar на Piper із українським чоловічим голосом `uk_UA-mykyta-high` та пресетом `jarvis_reference`, налаштованим за наданим відеореференсом. Модель і обробка працюють локально на CPU; сервіс слухає лише `127.0.0.1:8765`.

Це стилізація звуку, а не клонування голосу актора. Пресет окремо керує висотою основного тону й формантним забарвленням, а потім додає теплоту, вирізає «коробкові» середні частоти, підкреслює розбірливість, ущільнює динаміку та створює дуже короткий stereo ambience. Українська фонетика лишається за Piper, тому ефекти не замінюють і не перенавчають диктора.

## Встановлення і запуск

```powershell
cd D:\Jarvis\voice_service
.\install.ps1
.\start.ps1
```

Під час першого запуску офіційна модель (~114 МБ) і конфіг завантажаться з `rhasspy/piper-voices`. Файл моделі перевіряється за SHA-256.

Rust Desktop Core використовує той самий контракт: `POST /synthesize` з JSON `{"text":"..."}` повертає WAV. Тому `src/voice.rs` змінювати не потрібно.

## A/B-тест raw проти JARVIS FX

Коли sidecar запущений, у другому PowerShell:

```powershell
cd D:\Jarvis\voice_service
.\test_service.ps1
```

Скрипт створить по два файли для чотирьох українських фраз: `test_1_raw.wav` і `test_1_jarvis_reference.wav` тощо. Щоб одразу відтворити кожну пару, додайте `-Play`:

```powershell
.\test_service.ps1 -Play
```

За замовчуванням скрипт звертається до `http://127.0.0.1:8765`; іншу тестову адресу можна передати через `-BaseUrl`.

Для одного запиту режим також можна задати полем `mode`:

```json
{"text":"Усі системи працюють нормально.","mode":"jarvis_reference"}
```

Допустимі режими: `raw`, `jarvis_reference` і сумісний зі старою версією псевдонім `jarvis`. Поле `fx_enabled:false` примусово вимикає FX незалежно від режиму. Старий Rust-запит `{"text":"..."}` не змінився; без поля `mode` використовується пресет із `JARVIS_TTS_MODE`.

## Налаштування FX

Змінні задаються перед `.\start.ps1`:

```powershell
$env:JARVIS_TTS_MODE = 'jarvis_reference'
$env:JARVIS_TTS_SPEED = '1.03'
$env:JARVIS_FX_ENABLED = 'true'
$env:JARVIS_FX_PITCH_SEMITONES = '-1.10'
$env:JARVIS_FX_FORMANT_SHIFT_SEMITONES = '-0.70'
$env:JARVIS_FX_PRESERVE_FORMANTS = 'true'
$env:JARVIS_FX_LOW_SHELF_DB = '2.4'
$env:JARVIS_FX_WARMTH_DB = '1.4'
$env:JARVIS_FX_BOXINESS_DB = '-2.2'
$env:JARVIS_FX_PRESENCE_DB = '2.3'
$env:JARVIS_FX_AIR_DB = '0.8'
$env:JARVIS_FX_COMPRESSOR_THRESHOLD_DB = '-21.0'
$env:JARVIS_FX_COMPRESSOR_RATIO = '3.0'
$env:JARVIS_FX_SATURATION_DRIVE_DB = '3.0'
$env:JARVIS_FX_SATURATION_MIX = '0.08'
$env:JARVIS_FX_REVERB_ROOM_SIZE = '0.10'
$env:JARVIS_FX_REVERB_WET_LEVEL = '0.035'
$env:JARVIS_FX_SPATIAL_DELAY_MS = '7.0'
$env:JARVIS_FX_SPATIAL_WIDTH = '0.12'
$env:JARVIS_FX_OUTPUT_GAIN_DB = '-1.2'
.\start.ps1
```

Для чистого Piper при кожному запиті достатньо `JARVIS_FX_ENABLED=false` або `JARVIS_TTS_MODE=raw`. `JARVIS_TTS_SAMPLE_RATE=native` залишає рідні 22,05 кГц; за потреби можна вказати частоту від 8000 до 48000. Raw WAV залишається mono, а `jarvis_reference` повертає stereo WAV із центральним голосом і короткими ранніми відбиттями; Rust/rodio відтворює обидва формати через той самий HTTP контракт.

## Автоматична перевірка

Швидкі тести не потребують запущеного sidecar або аудіопристрою:

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_app.py
```

Наскрізний playback-тест Rust запускається окремо після старту sidecar:

```powershell
cd D:\Jarvis
cargo test voice_service_playback -- --ignored --nocapture
```

Для sidecar на іншому порту перед тестом задайте `JARVIS_TTS_TEST_URL`.

## Наскрізний запуск Desktop Core

Після запуску Voice Core відкрийте другий PowerShell:

```powershell
cd D:\Jarvis
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
cargo run
```

Якщо sidecar недоступний, Desktop Core продовжить працювати текстом і покаже попередження Voice Core. Для повного вимкнення озвучення встановіть `JARVIS_TTS_ENABLED=false` у `.env`.
