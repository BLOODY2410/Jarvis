# JARVIS v2

JARVIS v2 is a small Windows assistant built around Gemini 3.8 Live.

```text
local wake / VAD -> Gemini Live -> validated Python tool -> verified ToolResult -> reply/audio
```

Gemini understands Ukrainian, Russian insertions, surzhyk, app names, screen context and live questions. Python owns every action. A model response is never treated as evidence that an application, URL, file, or system setting changed.

## Run

Install Python 3.12 and [uv](https://docs.astral.sh/uv/), then:

```powershell
Copy-Item .env.example .env
# Set GEMINI_API_KEY in .env
uv sync --extra voice --extra windows --extra dev
uv run jarvis
```

`start.ps1` performs the same sync and starts one process. `stop.ps1` only stops that one process. There are no localhost sidecars or service ports.

## Windows app

For a simple launcher and settings window, build `JARVIS.exe` once:

```powershell
.\build-gui.ps1
```

Open `dist\JARVIS.exe`. It is a standalone Windows application: enter the Gemini key in its settings and it stores `.env`, logs and memory next to the executable. It starts the same one-process runtime without localhost sidecars or a terminal.

Fish is optional and deliberately excluded from the normal installation because its dependency stack is large. To use `JARVIS_VOICE_MODE=fish`, install and run with its extra:

```powershell
uv sync --extra voice --extra windows --extra fish
uv run jarvis
```

## Architecture

- **Gemini 3.8 Live** is the primary audio-to-audio, vision, web, and tool-calling brain.
- **Gemini 3.8 Live Extended Thinking** is reserved for explicit complex workflows.
- **Python tools** validate Pydantic arguments, execute a real Windows operation, and return `ToolResult`.
- **Groq** is an optional emergency text/STT fallback. It is never ahead of Gemini.
- **Fish Audio** is optional for the `fish` output path; native Gemini audio is the default.
- **Memory** retains a short session, recent tools/entities, and only grounded facts with source URLs.

The local fast path intentionally covers only safe, obvious actions: known websites/apps, volume, mute, media, settings and screenshots. `Відкрий новини` is a current-information request, never an invented executable. Damaged input such as `Við grey YouTube` asks for clarification and cannot produce a false acknowledgement.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [MIGRATION.md](MIGRATION.md).

## Verification

```powershell
uv run --extra dev pytest tests_v2
uv run --extra dev ruff check jarvis_v2 tests_v2
uv run jarvis --doctor
```

The automated suite uses mock SDK sessions and does not claim a successful cloud call. A Gemini Live voice session needs a real `GEMINI_API_KEY`, microphone permission and an interactive Windows audio check.
