# JARVIS v2 — Python-first Desktop Assistant

JARVIS v2 is now the default launcher. It runs microphone/VAD/STT, AI routing, validated Windows tools, Fish/Piper TTS, memory, and playback in one Python 3.12 process. The legacy Rust core remains in the repository and is still runnable during acceptance testing.

```text
Microphone -> VAD -> Groq Whisper primary/fallback -> Pydantic intent
                                                   |-> deterministic Fast Path -> validated Windows tool
                                                   |-> PC Agent tool loop
                                                   |-> conversation
                                                   |-> Gemini Search -> Groq Compound Mini
                                                   `-> screenshot -> vision
                                                          |
                                                   Fish TTS -> Piper fallback -> speaker
```

## Quick start

```powershell
cd D:\Jarvis
.\install.ps1
.\test.ps1
.\start.ps1
```

Useful commands:

```powershell
.\status.ps1
.\stop.ps1
.\benchmark-v2.ps1
.\.venv\Scripts\python.exe -m jarvis_v2 --text "постав гучність на 30"
.\.venv\Scripts\python.exe -m jarvis_v2 --doctor
```

`--doctor` is secret-safe and makes no cloud request. It reports configured provider names, whether voice/TTS are enabled, and confirms that v2 uses no localhost sidecars.

## Reliability rules

- Fast commands never wait for an LLM.
- Semantic actions must validate as a discriminated Pydantic intent with sufficient confidence.
- Tool calls are validated against strict schemas immediately before execution.
- Only a successful `ToolResult` can produce an action acknowledgement; model text is never evidence of success.
- Live answers require returned provenance URLs. Without sources, JARVIS explicitly refuses to present the answer as current.
- Provider calls use short timeouts, fallback chains, and circuit breakers.
- Voice budgeting keeps whole sentences; it never truncates in the middle of a sentence.
- “сер” is sparse and cannot appear in consecutive short replies.

## Voice and language

The default STT mode is automatic multilingual recognition for Ukrainian, Russian, and surzhyk. Short speech uses Groq `whisper-large-v3-turbo`; longer audio or a failed primary attempt falls back to `whisper-large-v3`. Add names and application vocabulary through `JARVIS_CUSTOM_VOCABULARY`.

Fish Audio and Piper behavior is preserved, but both are called directly inside the process. v2 does not listen on ports 8765/8766 and therefore avoids sidecar startup races and stale-port reuse.

## Providers

All keys are optional individually; a route uses the configured providers in its chain. The implementation follows the official Python SDK/API contracts for [Gemini and Google Search grounding](https://ai.google.dev/gemini-api/docs/get-started), [Groq local tool calling](https://console.groq.com/docs/tool-use/local-tool-calling), [Groq speech-to-text](https://console.groq.com/docs/speech-to-text), [Groq Compound](https://console.groq.com/docs/compound), [Cerebras tool calling](https://inference-docs.cerebras.ai/capabilities/tool-use), [Cerebras structured output](https://inference-docs.cerebras.ai/capabilities/structured-outputs), [Mistral SDK](https://docs.mistral.ai/resources/sdks), and [OpenRouter Python SDK](https://openrouter.ai/docs/client-sdks/python/overview).

Cloud tests are mock/contract tests unless explicitly run with real keys. The project never labels a mocked test as a successful live call.

See [MIGRATION.md](MIGRATION.md) for provider chains, rollback, and the acceptance checklist.

## Legacy Rust core

The rest of this document describes the preserved v1 implementation. Use `start-legacy.ps1`, `status-legacy.ps1`, and `stop-legacy.ps1` while comparing behavior.

# JARVIS Desktop Core (legacy v1)

Надійний режим для Windows зараз працює так:

- wake-фраза «Джарвіс», `Ctrl+Alt+J` або `POST /activate` активує слухання;
- системний мікрофон вибирається автоматично, а Stereo Mix/loopback відсіюються;
- capture/VAD працює 20-мс кадрами; 480 мс pre-roll і 240 мс post-roll не обрізають слова;
- короткий запис завершується після 440 мс тиші, а після 3 с мовлення timeout адаптивно зростає до 1,2 с;
- коротке аудіо йде у Groq `whisper-large-v3-turbo`, довге або невдала turbo-спроба — у `whisper-large-v3`;
- Rust спочатку перевіряє локальний Fast Command Engine і лише за потреби викликає Multi-Model AI Router;
- проста дія стартує одразу після intent match, а коротке TTS-підтвердження не генерується LLM;
- Fish Audio віддає динамічні відповіді через PCM streaming, короткі підтвердження читаються з локального кешу, а Piper ліниво готується як offline fallback;
- платний Fish fallback заборонений, доки явно не задано `FISH_AUDIO_ALLOW_PAID_FALLBACK=true`;
- мікрофон закритий під час обробки команди, TTS і короткого post-TTS guard.
- 60-секундний configurable idle timeout рахується лише тоді, коли JARVIS чекає наступну репліку в `LISTENING`.

Production-режим `JARVIS_ACTIVATION_MODE=hybrid` тримає wake-фразу та глобальну клавішу активними одночасно. `openWakeWord hey_jarvis` і український Vosk завантажуються у фоні; якщо один або обидва wake-backend не готові, `Ctrl+Alt+J` продовжує працювати. Wake detector лише відкриває `LISTENING` і ніколи сам не запускає інструмент. Окремі режими `hotkey`, `wake` та `api` збережено.

## Один запуск

У `D:\Jarvis\.env` має бути принаймні один AI key (`GROQ_API_KEY`, `CEREBRAS_API_KEY` або `GEMINI_API_KEY`) та чинні Fish-параметри для хмарного TTS. Секрети читаються безпосередньо з `.env` і не показуються у статусі чи логах. Перший раз:

```powershell
cd D:\Jarvis
.\install.ps1
```

Надалі потрібна одна команда:

```powershell
cd D:\Jarvis
.\start.ps1
```

Скрипт використовує єдине середовище `D:\Jarvis\.venv`, перевіряє `.env`, ідентичність процесів на 8765/8766, ротує великі логи й запускає Rust Core. `Ctrl+C` завершує Core і локальні сервіси, які запустив цей скрипт. Окремо доступні `status.ps1` і `stop.ps1`. Для Fish використовується окрема cinematic-швидкість `FISH_AUDIO_SPEED=0.97`; Piper і далі має власний `JARVIS_TTS_SPEED`.

Для одного сценарію «тести + запуск» використовуйте:

```powershell
cd D:\Jarvis
.\test-and-start.ps1
```

Безпечний статус TTS доступний на `http://127.0.0.1:8765/health`; `engine`, `active_provider`, `model`, `reference`, `cache` та причина останнього fallback не містять API-ключа.

## Тест 1-2-3

1. Скажіть «Джарвіс» або натисніть `Ctrl+Alt+J` і дочекайтеся короткого локального сигналу.
2. Одразу скажіть одним реченням: «Відкрий YouTube».
3. Переконайтеся, що команда з'явилася після `Ви:`, виконалась, а Jarvis відповів голосом. Повторіть 10 разів; ціль — щонайменше 9 правильних transcript.

У production WAV не пишуться на диск. Для діагностики тимчасово задайте `JARVIS_PROFILE=debug`; тоді WAV dumps, рівні й переходи станів будуть доступні. Вибраний пристрій видно на `http://127.0.0.1:8766/diagnostics`.

Screen Vision не реалізовано; `Vision` лишається зарезервованим маршрутом. Додано лише короткочасний shared conversation context без довготривалої пам'яті.

## Fast Command Engine

Без LLM локально виконуються: відкриття/закриття програм, відомі сайти й домени, абсолютна та відносна гучність, mute/unmute, список запущених програм і знімок екрана. Безпечний fuzzy matcher виправляє типові STT-помилки лише для whitelist дієслів та відомих aliases. Приклади: «Відкрай YouTube», «Відкрей Visual Studio Code», «Можеш відкрити Chrome», «Давай Steam», «Зроби тихіше».

Складені чіткі PC-команди переходять у Computer Agent із єдиним `ToolRegistry`; неясні фрази Safety Guard відхиляє без дії. У консолі `[Latency]` показує шлях від `speech_end` до STT, intent, tool і TTS playback. Samples пишуться у `logs\latency.jsonl`; `benchmark.ps1` рахує median/P95 та перевіряє цілі 1200/1500 мс.

## Multi-Model Brain v1

```text
Voice -> STT -> Fast/Safety Router -> AiRouter
                                  |-> Computer Agent: Cerebras -> Groq -> optional providers
                                  |-> Conversation: Gemini -> Groq -> optional providers
                                  `-> Live: Gemini 2.5 Flash + Google Search -> honest local guard
```

Маршрут визначається локальними правилами без окремого LLM-виклику. Fast Path не торкається AI API. Computer Agent — єдиний маршрут, якому передаються схеми локальних tools; conversation і live не можуть випадково керувати Windows. `ConversationContext` спільний для всіх провайдерів, обрізається до `JARVIS_MAX_CONTEXT_TURNS` і зберігає компактні події на кшталт відкритої програми чи сайту.

Підтверджені стандартні моделі:

- Cerebras `gpt-oss-120b` — primary PC/tool brain;
- Gemini `gemini-3.6-flash` — smart conversation;
- Gemini `gemini-2.5-flash` — live route з Google Search у free tier;
- Groq `qwen/qwen3.8-27b` — multilingual/tool-capable fallback.

Model IDs та API contracts звірені з офіційними сторінками [Cerebras models](https://inference-docs.cerebras.ai/models/overview), [Cerebras authentication](https://inference-docs.cerebras.ai/api-reference/authentication), [Gemini 3.6 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash), [Gemini pricing/Search quota](https://ai.google.dev/gemini-api/docs/pricing) і [Groq Qwen 3.8](https://console.groq.com/docs/model/qwen/qwen3.8-27b).

Усі ключі незалежні й optional. OpenRouter та Mistral активуються лише коли одночасно задані відповідні key і model; core не прив'язаний до мінливої free-model list. Якщо є тільки старий `GROQ_API_KEY`, усі non-live запити автоматично підуть у Groq, а live-запити отримають чесну anti-hallucination відповідь.

Health state не перевіряє провайдерів при запуску. Після 429 або timeout провайдер тимчасово пропускається; auth error вимикає його до перезапуску. У логах видно route/provider/model/latency/fallback і token usage, якщо API його повернув, але ніколи не видно ключів або raw provider error JSON.

Поточну конфігурацію без секретів показує `status.ps1`, startup-рядок або команда `Статус AI Router`. Доступність live-даних залежить від Gemini Search quota. Grounding sources додаються до текстової відповіді; голос озвучує лише коротке резюме.

Основні змінні наведені у `.env.example`: `JARVIS_*_BRAIN_PROVIDER`, `JARVIS_AI_FALLBACK_ORDER`, per-provider model/key, timeouts, cooldowns, context і output budgets. Для legacy-запуску достатньо залишити `GROQ_API_KEY` та запустити звичайний launcher.

Router benchmark без реальних tool-дій:

```powershell
cd D:\Jarvis
.\router-benchmark.ps1
```

За замовчуванням harness перевіряє 25 expected routes/tools без API-викликів. `./router-benchmark.ps1 -LiveProviders` додатково збирає provider/model, latency, token usage, tool selection і fallback count; локальні tools при цьому не виконуються, але витрачається API quota.

Для автоматичної перевірки build/unit/integration tests:

```powershell
cd D:\Jarvis
.\test.ps1
```

Після щонайменше 20 реальних голосових команд запустіть `./benchmark.ps1`.
