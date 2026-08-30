# JARVIS Voice Input Core

Локальний Windows-sidecar для мікрофона, VAD, wake word і Groq Whisper. За замовчуванням працює half-duplex: коли Jarvis говорить через колонки, запис команд призупинений, а після TTS діє короткий guard. Barge-in не видалений і вмикається через `JARVIS_BARGE_IN_ENABLED=true`, але для колонок його слід лишати вимкненим.

## Звичайний і діагностичний запуск

```powershell
cd D:\Jarvis\voice_input_service
.\install.ps1
.\start.ps1
```

Для стабілізації мікрофона використовуйте окремий режим:

```powershell
cd D:\Jarvis\voice_input_service
.\start_diagnostic.ps1
```

Якщо порт 8766 зайнятий, скрипт покаже PID. У старому вікні Voice Input Core натисніть `Ctrl+C`, після чого повторіть запуск.

Діагностичний лог показує:

- усі input devices, системний default і фактично відкритий мікрофон;
- 16 кГц, mono, int16, chunk 1280 samples / 80 мс;
- RMS і peak як частку full scale та в dBFS, стан VAD і wake score;
- початок/кінець запису, voiced/raw/Whisper duration, pre-roll/post-roll;
- точний transcript та весь metadata JSON від `verbose_json` (`avg_logprob`, `no_speech_prob`, timestamps, якщо Groq їх повернув);
- стан barge-in і період, коли input приглушений через TTS/guard.

Останні файли зберігаються в `D:\Jarvis\voice_input_service\diagnostics`:

- `last_raw.wav` — весь необрізаний VAD-сеанс із pre-roll і повною кінцевою тишею;
- `last_whisper.wav` — рівно той WAV, який було відправлено у Groq Whisper;
- `history\*` — пари попередніх raw/Whisper записів для порівняння.

## Вибір мікрофона та gain

```powershell
cd D:\Jarvis\voice_input_service
.\list_microphones.ps1
```

Запустити діагностику з конкретним індексом, наприклад `1`:

```powershell
.\start_diagnostic.ps1 -Device 1
```

Або збережіть індекс/унікальну частину назви у `D:\Jarvis\.env` як `JARVIS_MIC_DEVICE=1`. Не вибирайте `Stereo Mix` або output/loopback device: вони захоплюють звук колонок і створюють самопрослуховування.

Відкрийте Windows `Параметри → Система → Звук → Вхід`, переконайтеся, що вибрано той самий пристрій, і перевірте input volume:

```powershell
Start-Process 'ms-settings:sound'
```

Практичний орієнтир: у тиші RMS має бути низьким і стабільним; під час нормальної мови peak не повинен постійно доходити до `1.0000 / 0 dBFS`. Якщо мова майже не відрізняється від тиші — підніміть input volume або наблизьте мікрофон. Якщо peak постійно біля 0 dBFS — зменшіть gain.

## Тест без wake word

Після запуску sidecar примусово активуйте ланцюжок `microphone → VAD → Whisper`:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8766/activate
```

Після події `wake` скажіть одну фразу. Поточну телеметрію можна прочитати так:

```powershell
Invoke-RestMethod http://127.0.0.1:8766/diagnostics | ConvertTo-Json -Depth 8
```

## Окремий тест wake detector на WAV

Щоб записати wake phrase окремо, викличте `/activate`, вимовте тільки «Джарвіс» і дочекайтеся створення `last_raw.wav`. Або підготуйте інший mono PCM WAV 16 кГц/16-bit. Потім, не змінюючи поріг, виміряйте реальний score:

```powershell
.\test_wake_wav.ps1 -WavPath .\diagnostics\last_raw.wav
```

Для порівняння конкретного порога без зміни `.env`:

```powershell
.\test_wake_wav.ps1 -WavPath .\diagnostics\last_raw.wav -Threshold 0.35
```

Результат містить `max_openwakeword_score`, десять найсильніших точок, Vosk hypotheses і час детекції. Поріг `JARVIS_WAKE_THRESHOLD` змінюйте лише після кількох записів «Джарвіс» та кількох негативних фраз/кімнатного шуму. Фонетичні варіанти Vosk видно в `vosk_hypotheses`; додавати їх у matcher слід лише якщо вони повторюються на позитивних записах і не виникають на негативних.

Додаткові підтверджені варіанти задаються через кому, наприклад `JARVIS_WAKE_VARIANTS=джарвіс,джарвис,джарвиз,підтверджений_варіант`. `JARVIS_WAKE_VOSK_MAX_EDIT_DISTANCE=1` дозволяє одну помилку Vosk у слові (наприклад, пропущену літеру), але не приймає віддалені слова на кшталт «джерело». Значення `0` повертає точний match; вище `1` без окремого тестування ставити не варто.

## Перевірка базового сценарію 9/10

Зробіть десять однакових циклів із паузами між кроками:

1. «Джарвіс».
2. Дочекайтеся відповіді активації.
3. «Відкрий YouTube».
4. У наступному циклі замініть команду на «Скажи, що голос працює».
5. Для кожного циклу відмітьте wake detection, `Whisper transcript EXACT`, виконання і TTS.

Спершу добийтеся 9/10 правильних transcript через `/activate`. Лише потім оцінюйте wake word окремо. Це відділяє помилки microphone/VAD/Whisper від помилок wake detector.

## Налаштування VAD та echo

Основні змінні у `D:\Jarvis\.env`:

```dotenv
JARVIS_WAKE_THRESHOLD=0.45
JARVIS_WAKE_VARIANTS=джарвіс,джарвис,джарвиз
JARVIS_WAKE_VOSK_MAX_EDIT_DISTANCE=1
JARVIS_VAD_AGGRESSIVENESS=1
JARVIS_VAD_ENERGY_RATIO=1.10
JARVIS_VAD_ENERGY_DELTA=0.004
JARVIS_VAD_START_CHUNKS=1
JARVIS_END_SILENCE_MS=1200
JARVIS_PRE_ROLL_MS=560
JARVIS_POST_ROLL_MS=480
JARVIS_MIN_SPEECH_MS=300
JARVIS_MIN_AUDIO_RMS=0.0015
JARVIS_BARGE_IN_ENABLED=false
JARVIS_POST_TTS_GUARD_MS=250
```

Поверх WebRTC VAD працює адаптивний energy gate. Він вимірює фоновий RMS у режимі очікування та обережно продовжує оновлювати rolling median під час активної розмовної сесії на кадрах нижче поточного energy-порога. Це працює навіть тоді, коли WebRTC помилково вважає сталий шум Realtek голосом. Speech проходить, коли рівень перевищує фон за ratio/delta. Так Whisper захищений від довгих записів шуму, а поріг не заморожується після wake word.

`pre-roll` додається перед першим speech-frame, а з кінцевої тиші до Whisper лишається щонайменше налаштований `post-roll` (інтервали округлюються вгору до цілого 80-мс кадру). Поки попередня репліка обробляється Whisper, новий capture явно призупинений: сервіс не накопичує урізану команду і відновлює слухання після завершення STT. Дочекайтеся виконання попередньої команди перед наступною реплікою.

Якщо `last_whisper.wav` усе ще не має першого складу — збільшуйте `JARVIS_PRE_ROLL_MS` кроками по `80`. Якщо обрізано кінець — так само збільшуйте `JARVIS_POST_ROLL_MS` або `JARVIS_END_SILENCE_MS`. Якщо в записі чути TTS з колонок, barge-in лишіть `false`; навушники/направлений мікрофон дозволяють перевірити `true` без зміни коду.

## API і тести

- `GET /devices` — input devices і фактично вибраний мікрофон;
- `GET /diagnostics` — рівні, VAD, конфіг і останній transcript;
- `POST /activate` — обхід wake word;
- `POST /diagnostic/wake-wav` — офлайн-вимір wake detector на WAV;
- `POST /state`, `GET /events/next`, `POST /stop` — контракт Desktop Core.

```powershell
cd D:\Jarvis\voice_input_service
.\.venv\Scripts\python.exe -m unittest -v test_app.py
```
