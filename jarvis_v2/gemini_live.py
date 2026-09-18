"""Native Gemini Live session with an explicit, verified local tool loop.

The Live API does not execute tools for the client. This module deliberately keeps
that boundary visible: Gemini requests, Python validates and executes, then Gemini
receives the exact ToolResult. It is safe to test with a mocked session and needs a
real API key only when a session is opened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jarvis_v2.config import Settings
from jarvis_v2.models import GroundingSource, ToolResult
from jarvis_v2.tools import ToolRegistry


@dataclass(slots=True)
class LiveTurn:
    text: str = ""
    audio_pcm: list[bytes] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    sources: list[GroundingSource] = field(default_factory=list)


class GeminiLiveSession:
    def __init__(self, settings: Settings, tools: ToolRegistry, *, extended: bool = False) -> None:
        self.settings = settings
        self.tools = tools
        self.model = settings.gemini_extended_model if extended else settings.gemini_live_model
        self._client: object | None = None
        self._connection: Any = None
        self._session: Any = None
        self.action_dispatched = False

    def _client_for_key(self) -> object:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    @staticmethod
    def _function_declarations(schemas: list[dict[str, object]]) -> list[dict[str, object]]:
        declarations: list[dict[str, object]] = []
        for schema in schemas:
            function = schema.get("function")
            if not isinstance(function, dict):
                continue
            declaration = dict(function)
            declaration.pop("strict", None)
            # Windows mutations are sequential: Gemini receives the authoritative
            # receipt before it can plan the next action.
            declaration["behavior"] = "BLOCKING"
            declarations.append(declaration)
        return declarations

    async def connect(self, system_instruction: str, *, native_audio: bool = True) -> None:
        """Open one persistent WebSocket. The caller owns the session lifetime."""
        if self._session is not None:
            return
        if not self.settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Gemini Live.")
        tools: list[dict[str, object]] = [
            {"function_declarations": self._function_declarations(self.tools.schemas())},
            {"google_search": {}},
        ]
        config = {
            "response_modalities": ["AUDIO"] if native_audio else ["TEXT"],
            "system_instruction": system_instruction,
            "tools": tools,
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }
        self._connection = self._client_for_key().aio.live.connect(model=self.model, config=config)  # type: ignore[attr-defined]
        self._session = await self._connection.__aenter__()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.__aexit__(None, None, None)
        self._connection = None
        self._session = None

    async def send_audio(self, pcm_16khz: bytes) -> None:
        if self._session is None:
            raise RuntimeError("Gemini Live session is not connected.")
        from google.genai import types

        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm_16khz, mime_type="audio/pcm;rate=16000")
        )

    async def send_text(self, text: str) -> None:
        if self._session is None:
            raise RuntimeError("Gemini Live session is not connected.")
        await self._session.send_client_content(turns={"role": "user", "parts": [{"text": text}]}, turn_complete=True)

    async def receive_turn(self) -> LiveTurn:
        if self._session is None:
            raise RuntimeError("Gemini Live session is not connected.")
        from google.genai import types

        turn = LiveTurn()
        async for response in self._session.receive():
            content = getattr(response, "server_content", None)
            if content:
                transcription = getattr(content, "output_transcription", None)
                if transcription and getattr(transcription, "text", None):
                    turn.text += str(transcription.text)
                model_turn = getattr(content, "model_turn", None)
                for part in getattr(model_turn, "parts", []) if model_turn else []:
                    inline = getattr(part, "inline_data", None)
                    if inline and getattr(inline, "data", None):
                        turn.audio_pcm.append(bytes(inline.data))
                    if getattr(part, "text", None):
                        turn.text += str(part.text)
                if getattr(content, "turn_complete", False):
                    return turn
            tool_call = getattr(response, "tool_call", None)
            calls = getattr(tool_call, "function_calls", []) if tool_call else []
            if calls:
                responses = []
                for call in calls:
                    self.action_dispatched = True
                    result = self.tools.execute(str(call.name), dict(getattr(call, "args", {}) or {}))
                    turn.tool_results.append(result)
                    responses.append(
                        types.FunctionResponse(
                            id=str(call.id), name=str(call.name), response={"result": result.model_dump()}
                        )
                    )
                await self._session.send_tool_response(function_responses=responses)
        return turn

    async def text_turn(self, text: str) -> LiveTurn:
        self.action_dispatched = False
        await self.send_text(text)
        return await self.receive_turn()
