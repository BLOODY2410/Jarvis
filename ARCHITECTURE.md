# JARVIS v2 architecture

JARVIS is one Python application. It opens no internal HTTP port.

```text
Microphone -> local wake/VAD -> Gemini 3.8 Live
                                     | tool request
                                     v
                                Python ToolRegistry
                                     | validated ToolResult
                                     v
                               Gemini reply / native audio
```

The primary Live session sends local 16 kHz PCM only after activation. Gemini receives declared tool schemas. Each requested function is validated by Pydantic immediately before execution; the exact result is sent back through the Live tool-response API. This prevents model prose from claiming an action that did not happen.

Power actions are never declared to Gemini directly. The host creates a short-lived pending action from canonical validated arguments and only an explicit subsequent user confirmation can consume it. Windows launch receipts say that Windows accepted a request; they do not assert that the target application became usable. Native model audio remains generative output, so the system guarantees action execution receipts, not a formal proof of every free-form spoken sentence.

`jarvis_v2/gemini_live.py` owns the Live WebSocket protocol. `jarvis_v2/tools.py` owns Windows actions and safety boundaries. `jarvis_v2/agent.py` owns text fallback routing, context and provenance. `jarvis_v2/voice.py` owns direct microphone/VAD/playback without a sidecar.

The normal model is `gemini-3.8-live`. `gemini-3.8-live-extended-thinking` remains separate so high reasoning is only used when a future explicit complex-workflow gate selects it. The text/CLI companion is `gemini-3.8-flash`; it does not replace Live voice mode.

Grounded answers are saved only with returned URLs. Secrets, raw audio and API headers are never written to session memory or logs.
