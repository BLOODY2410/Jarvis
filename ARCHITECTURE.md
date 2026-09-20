# JARVIS v2 architecture

JARVIS is one Python application. It opens no internal HTTP port.

```text
Microphone -> local wake/VAD -> transcript preflight (when a local transcript exists)
                                      | obvious safe command -> Python ToolRegistry
                                      v
                                 Gemini 3.8 Live
                                      | tool request / grounding metadata
                                      v
                                host-owned ToolResult + session memory
                                      |
                                      v
                              streaming native audio or Fish output
```

The primary Live session sends local 16 kHz PCM only after activation. Gemini receives declared tool schemas. Each requested function is validated by Pydantic immediately before execution; the exact result is sent back through the Live tool-response API. This prevents model prose from claiming an action that did not happen.

Power actions are never declared to Gemini directly. The host creates a short-lived pending action from canonical validated arguments and only an explicit subsequent user confirmation can consume it. Windows launch receipts say that Windows accepted a request; they do not assert that the target application became usable. Native model audio remains generative output, so the system guarantees action execution receipts, not a formal proof of every free-form spoken sentence.

`jarvis_v2/gemini_live.py` owns the Live WebSocket protocol. `jarvis_v2/tools.py` owns Windows actions and safety boundaries. `jarvis_v2/agent.py` owns shared execution, confirmation, context, persona, memory and provenance hooks. `jarvis_v2/voice.py` owns direct microphone/VAD and a non-blocking output queue without a sidecar. Live grounding is read only from the official `grounding_metadata.grounding_chunks[].web` fields; a current-information answer is retained only when those fields provide HTTP(S) URLs.

The normal model is `gemini-3.8-live`. `gemini-3.8-live-extended-thinking` is selected only for an explicit multi-step inspect/decide/execute request or screen-error remediation; everyday conversation and direct actions remain on normal Live. Extended sessions use the SDK's required non-blocking function declarations and wait for `interaction_status=IDLE`. The text/CLI companion is `gemini-3.8-flash`; it does not replace Live voice mode.

The deterministic voice preflight is available whenever the local activation layer supplies a transcript. This repository does not bundle a Ukrainian local STT model, so raw microphone input currently receives its transcript from Gemini Live after activation. It must not claim a zero-cloud-latency fast path for raw audio. The preflight remains the single small safe route for hotkey/UI-supplied transcripts and future compatible local recognition.

Before a Windows release, perform this manual checklist: activate microphone; connect a real Gemini Live key; execute one fast-path command; verify a Live tool receipt; hear native streaming audio and barge in; run Fish output on Gemini intelligence; inspect a screenshot; ask a web question with search enabled; and force a Live failure to verify emergency fallback. Cloud tests are mocks.

Grounded answers are saved only with returned URLs. Secrets, raw audio and API headers are never written to session memory or logs.
