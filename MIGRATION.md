# Migration from Rust v1

The active implementation is Python-only. The complete Rust v1 source remains recoverable at Git tag `legacy-rust-v1`.

```powershell
git show legacy-rust-v1:README.md
git switch --detach legacy-rust-v1
```

## Removed from v2

- Rust core, Cargo build, executable replacement flow and the provider router.
- The Cerebras, Mistral and OpenRouter adapter zoo.
- HTTP voice-input and TTS sidecars on ports 8765/8766.
- The broad command matcher that turned unknown nouns into executable names.
- Legacy startup, status and stop flows built around stale processes.

## Preserved behavior

- Local activation, VAD, short active-conversation window and playback interruption hook.
- Known wake spellings, safe fast commands, Windows settings URIs, volume/media control, screenshots and protected-process guards.
- Small local AppIndex behaviour: known aliases, Start Menu shortcuts and PATH dispatch; unknown names are not executed.
- Fish as an optional output path, with a local Windows speech fallback if Fish is unavailable.
- Useful timing boundaries and short, complete voice replies.

## Setup and validation

Use `.env.example` as the complete current configuration. The primary key is `GEMINI_API_KEY`; Groq and Fish are optional. Run `uv run jarvis` after `uv sync --extra voice --extra windows`.

The test suite verifies tool validation, no-fake-success, damaged STT recovery, UA/RU/surzhyk fast intents, multi-intent ordering, provenance persistence, sentence budgeting and the Gemini Live tool-result cycle. It deliberately mocks cloud calls. Before a release, test a real microphone, native Live audio, at least one validated action, screenshot vision and optional Fish output with the target account.

Known operational limitation: the optional OpenWakeWord package does not currently ship a compatible CPython 3.12 wheel in this environment. Ctrl+Alt+J remains the reliable local activation path until a compatible Ukrainian wake backend is installed; the microphone is not sent to Gemini before activation.
