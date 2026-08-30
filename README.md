# JARVIS Desktop Core

Rust лишається головним агентом і виконує Windows-інструменти, діалог та керування TTS. Два локальні Python-sidecar ізольовують практичні аудіозалежності:

- `voice_service` — наявний TTS-контракт `POST /synthesize` на `127.0.0.1:8765` (не змінений);
- `voice_input_service` — мікрофон, VAD, wake word, Groq Whisper та керований barge-in на `127.0.0.1:8766`.

У фоні Whisper не працює. До активації обчислюється лише локальний wake detector/VAD. Після відповіді розмовний режим лишається активним 25 секунд, тому «Джарвіс» не треба повторювати перед наступною реплікою.

## Повний запуск

Переконайтеся, що в `D:\Jarvis\.env` є `GROQ_API_KEY`. Одноразово встановіть Voice Input Core:

```powershell
cd D:\Jarvis\voice_input_service
.\install.ps1
```

Запустіть три PowerShell-вікна:

```powershell
cd D:\Jarvis\voice_service
.\start.ps1
```

```powershell
cd D:\Jarvis\voice_input_service
.\start.ps1
```

```powershell
cd D:\Jarvis
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
cargo run
```

Якщо Voice Input Core недоступний або в `.env` задано `JARVIS_VOICE_INPUT_ENABLED=false`, Rust автоматично відкриває старий текстовий цикл. Недоступний TTS не зупиняє діалог і лише дає попередження.

## Перевірка сценарію

1. Скажіть: «Джарвіс» (або «Хей, Джарвіс»), потім: «Відкрий YouTube».
2. Дочекайтеся виконання та початку голосової відповіді.
3. Якщо використовуються навушники й задано `JARVIS_BARGE_IN_ENABLED=true`, під час відповіді скажіть: «Ні, відкрий у новій вкладці» — аудіо має зупинитися, а нова команда виконатися. З колонками barge-in лишається вимкненим, щоб Jarvis не чув сам себе.
4. Упродовж 25 секунд поставте ще одне питання без слова «Джарвіс».
5. Перевірте аварійну зупинку через `Ctrl+C` або `voice_input_service\stop.ps1`.

## Автоматичні перевірки

```powershell
cd D:\Jarvis
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
cargo test
```

```powershell
cd D:\Jarvis\voice_input_service
.\.venv\Scripts\python.exe -m unittest -v test_app.py
```

Screen Vision і керування мишкою до цього етапу не входять.

## Діагностика Voice Input Core

Для окремої перевірки мікрофона, VAD, WAV до/після обрізання, Whisper metadata та wake score:

```powershell
cd D:\Jarvis\voice_input_service
.\list_microphones.ps1
.\start_diagnostic.ps1
```

Повний сценарій стабілізації та пояснення логів є у `voice_input_service\README.md`.
