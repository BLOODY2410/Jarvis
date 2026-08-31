# Voice Input Core

Сервіс `127.0.0.1:8766` реалізує детермінований half-duplex шлях `мікрофон → adaptive VAD → Groq Whisper → Rust` на 20-мс кадрах. До 3,5 с озвученого тексту використовується `whisper-large-v3-turbo`, а довше аудіо та fallback використовують `whisper-large-v3`. Для коротких команд VAD завершує запис після 440 мс тиші; після 3 с мовлення застосовується довший configurable timeout.

Основний режим — `JARVIS_ACTIVATION_MODE=hybrid`: скажіть «Джарвіс» або натисніть `Ctrl+Alt+J`. Та сама дія доступна як `POST /activate`. Hotkey запускається незалежно від wake backend; openWakeWord і Vosk завантажуються у фоні, а їхня помилка не зупиняє сервіс. Під час TTS, виконання команди та Whisper новий запис не починається.

Корисні endpoint:

- `GET /health` — стан потоку, вибраний мікрофон і останній transcript;
- `GET /devices` — усі input-пристрої;
- `GET /diagnostics` — рівні, пороги та шляхи WAV;
- `POST /activate` — примусова активація;
- `POST /state` — контракт Rust Core для half-duplex.

Запускайте весь Jarvis тільки з кореня:

```powershell
cd D:\Jarvis
.\start.ps1
```

У `JARVIS_PROFILE=production` діагностичні WAV не створюються. Для тимчасової діагностики задайте `JARVIS_PROFILE=debug`; тоді WAV зберігаються у `diagnostics\last_raw.wav` та `diagnostics\last_whisper.wav`. Якщо автоматично вибрано не той пристрій, задайте `JARVIS_MIC_DEVICE` у `D:\Jarvis\.env`.

Окремі режими `hotkey`, `wake` та `api` лишаються доступними. Fuzzy/phonetic matching застосовується лише до configured wake-варіантів; wake-подія активує `LISTENING`, але не містить команди для виконання.

Налаштування: `JARVIS_FAST_END_SILENCE_MS` (400–600), `JARVIS_END_SILENCE_MS`, `JARVIS_LONG_UTTERANCE_THRESHOLD_MS`, `JARVIS_FAST_STT_MAX_SPEECH_MS`, `JARVIS_FAST_WHISPER_MODEL` і `JARVIS_WHISPER_MODEL`. Transcript event лишається сумісним (`type` + `text`) та додатково містить часові мітки для latency-логів Rust Core.
