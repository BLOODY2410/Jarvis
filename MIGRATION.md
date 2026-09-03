# Migration to JARVIS v2

JARVIS v2 is a new Python-first core, not a line-by-line Rust port. The legacy Rust implementation is intentionally retained until real microphone, TTS, provider, and Windows-action acceptance checks pass on the target PC.

## What changed

```text
Before: microphone -> Python HTTP :8766 -> Rust -> Python HTTP :8765 -> speakers
Now:    microphone -> one Python process -> validated intent/tool -> speakers
```

- `jarvis_v2/` owns routing, providers, memory, persona, tools, vision, observability, and the application loop.
- The proven `VoiceInputEngine` and `VoiceRouter` are composed directly in-process. Ports 8765 and 8766 are not used by v2.
- `start.ps1`, `stop.ps1`, and `status.ps1` target v2. Their previous implementations are preserved as `*-legacy.ps1`.
- `src/`, `Cargo.toml`, and all Rust tests remain intact.

## Installation

JARVIS v2 requires Windows and Python 3.12.

```powershell
cd D:\Jarvis
.\install.ps1
Copy-Item .env.example .env  # only when .env does not already exist
# Add only the API keys you actually use.
.\test.ps1
```

`install.ps1` installs the editable project and all Windows/voice/test extras with `uv`, then refreshes `uv.lock`. The lock is deliberately constrained to Windows because the application uses Windows audio, settings URIs, and hotkeys.

## Provider migration

Missing providers are skipped. Recommended chains are configured independently:

- intent: Cerebras -> Groq -> Gemini;
- PC agent: Cerebras -> Groq -> Gemini -> OpenRouter -> Mistral;
- conversation: Gemini -> Groq -> Cerebras -> OpenRouter -> Mistral;
- live: Gemini Google Search -> Groq Compound Mini;
- vision: Gemini -> OpenRouter.

Each call has an outer timeout and circuit breaker. A timeout or quota error falls through; an auth failure disables that provider for the process lifetime. No startup health call spends quota.

Do not treat `--doctor` or the unit tests as proof that cloud keys work. Live checks are intentionally manual because they spend quota and depend on account access.

## Safety and behavior changes

- Deterministic fast commands execute without an LLM.
- Ambiguous actions use a Pydantic JSON-schema classifier; output below the confidence threshold is rejected.
- Tool arguments are validated again immediately before execution.
- An action acknowledgement comes only from `ToolResult`. Model prose such as “YouTube відкрито” cannot create success.
- `Við grey YouTube` and similarly damaged action verbs request clarification and do nothing.
- “Відкрий новини” is a live-information request, never `open_app`.
- Live answers are shown as current only if provenance URLs were returned. Only such answers enter grounded memory.
- Opening an app or URL reports that Windows accepted the launch request; it does not claim the target became usable without verification.

## Voice/STT/TTS

Short audio uses `whisper-large-v3-turbo`; long audio and failed short-model calls use `whisper-large-v3`. `JARVIS_STT_LANGUAGE=auto` allows Ukrainian, Russian, and mixed surzhyk. `JARVIS_CUSTOM_VOCABULARY` is inserted into the documented Whisper prompt field.

Fish Audio remains primary when configured. Piper remains the local fallback. Both are invoked as Python objects rather than HTTP services. Voice responses are reduced only at complete sentence boundaries.

## Rollback

No source rollback is needed:

```powershell
.\stop.ps1
.\start-legacy.ps1
```

Legacy status and stop scripts are `status-legacy.ps1` and `stop-legacy.ps1`. Do not run v2 and legacy voice capture simultaneously.

## Acceptance checklist before removing Rust

1. Run `.\test.ps1`.
2. Run `.\benchmark-v2.ps1` and retain median/P95 output.
3. Test 20 wake-word/hotkey turns in Ukrainian, Russian, and surzhyk.
4. Verify Fish, forced Piper fallback, barge-in setting, and post-TTS guard.
5. Verify volume, mute, Settings pages, app launch/close, screenshot, vision, and multi-intent actions.
6. With real keys, verify timeout fallback and live provenance. Record failures honestly; do not replace contract tests with claimed live success.
7. Keep the Rust core until this checklist passes on the user's PC.
