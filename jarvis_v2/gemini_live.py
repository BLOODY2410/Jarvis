"""Native Gemini Live session with an explicit, verified local tool loop.

The Live API does not execute tools for the client. This module deliberately keeps
that boundary visible: Gemini requests, Python validates and executes, then Gemini
receives the exact ToolResult. It is safe to test with a mocked session and needs a
real API key only when a session is opened.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from jarvis_v2.config import Settings
from jarvis_v2.models import GroundingSource, ToolResult
from jarvis_v2.tools import ToolRegistry


@dataclass(slots=True)
class LiveTurn:
    text: str = ""
    user_text: str = ""
    audio_pcm: list[bytes] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    sources: list[GroundingSource] = field(default_factory=list)
    fast_handled: bool = False


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
    def _function_declarations(schemas: list[dict[str, object]], *, extended: bool = False) -> list[Any]:
        """Build SDK declarations without mutating JSON Schema field names."""
        from google.genai import types

        declarations: list[Any] = []
        for schema in schemas:
            function = schema.get("function")
            if not isinstance(function, dict):
                continue
            parameters = function.get("parameters")
            if not isinstance(parameters, dict):
                continue
            # Windows mutations are sequential: Gemini receives the authoritative
            # receipt before it can plan the next action.
            declarations.append(
                types.FunctionDeclaration(
                    name=str(function.get("name", "")),
                    description=str(function.get("description", "")),
                    parameters_json_schema=parameters,
                    # Extended Thinking accepts only asynchronous declarations.
                    behavior="NON_BLOCKING" if extended else "BLOCKING",
                )
            )
        return declarations

    async def connect(self, system_instruction: str, *, native_audio: bool = True) -> None:
        """Open one persistent WebSocket. The caller owns the session lifetime."""
        if self._session is not None:
            return
        if not self.settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Gemini Live.")
        from google.genai import types

        tools = [
            types.Tool(
                function_declarations=self._function_declarations(
                    self.tools.schemas(include_confirmable=True), extended=self.model == self.settings.gemini_extended_model
                )
            )
        ]
        if self.settings.web_enabled:
            tools.append(types.Tool(google_search=types.GoogleSearch()))

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"] if native_audio else ["TEXT"],
            system_instruction=system_instruction,
            tools=tools,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )
        self._connection = self._client_for_key().aio.live.connect(model=self.model, config=config)  # type: ignore[attr-defined]
        self._session = await self._connection.__aenter__()

    async def close(self) -> None:
        if self._connection is not None:
            # Some google-genai releases schedule a second close after a
            # failed connection before their async HTTP client exists.
            # The session is already unusable; do not let that cleanup defect
            # terminate the local voice loop.
            with suppress(AttributeError):
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

    async def send_context(self, guidance: str) -> None:
        """Inject compact host state without interrupting a live generation."""
        if self._session is None:
            raise RuntimeError("Gemini Live session is not connected.")
        await self._session.send_client_content(
            turns={"role": "user", "parts": [{"text": guidance}]}, turn_complete=False
        )

    @staticmethod
    def _grounding_sources(metadata: object) -> list[GroundingSource]:
        """Read only documented Live grounding_chunks.web metadata."""
        sources: dict[str, GroundingSource] = {}
        for chunk in getattr(metadata, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            url = getattr(web, "uri", None)
            if isinstance(url, str) and urlparse(url).scheme in {"http", "https"}:
                title = str(getattr(web, "title", None) or urlparse(url).netloc)
                sources[url] = GroundingSource(title=title, url=url)
        return list(sources.values())

    async def receive_turn(
        self,
        *,
        on_audio: Callable[[bytes], Awaitable[None]] | None = None,
        execute_tool: Callable[[str, dict[str, object]], Awaitable[ToolResult]] | None = None,
        on_input_transcript: Callable[[str], Awaitable[bool]] | None = None,
    ) -> LiveTurn:
        if self._session is None:
            raise RuntimeError("Gemini Live session is not connected.")
        from google.genai import types

        turn = LiveTurn()
        while True:
            async for response in self._session.receive():
                content = getattr(response, "server_content", None)
                if content:
                    transcription = getattr(content, "input_transcription", None)
                    if transcription and getattr(transcription, "text", None):
                        turn.user_text += str(transcription.text)
                        if getattr(transcription, "finished", True) and on_input_transcript:
                            turn.fast_handled = await on_input_transcript(turn.user_text)
                            if turn.fast_handled:
                                return turn
                    transcription = getattr(content, "output_transcription", None)
                    if transcription and getattr(transcription, "text", None):
                        turn.text += str(transcription.text)
                    model_turn = getattr(content, "model_turn", None)
                    for part in getattr(model_turn, "parts", []) if model_turn else []:
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            chunk = bytes(inline.data)
                            turn.audio_pcm.append(chunk)
                            if on_audio:
                                await on_audio(chunk)
                        if getattr(part, "text", None):
                            turn.text += str(part.text)
                    turn.sources.extend(self._grounding_sources(getattr(content, "grounding_metadata", None)))
                    # Extended Thinking can say turn_complete while reasoning continues.
                    status = str(getattr(content, "interaction_status", "IDLE"))
                    if getattr(content, "turn_complete", False) and (self.model != self.settings.gemini_extended_model or status.endswith("IDLE")):
                        return turn
                tool_call = getattr(response, "tool_call", None)
                calls = getattr(tool_call, "function_calls", []) if tool_call else []
                if calls:
                    responses = []
                    for call in calls:
                        self.action_dispatched = True
                        name = str(call.name)
                        arguments = dict(getattr(call, "args", {}) or {})
                        result = await execute_tool(name, arguments) if execute_tool else self.tools.execute(name, arguments)
                        if name == "screenshot" and result.success and isinstance(result.data.get("path"), str):
                            path = result.data["path"]
                            try:
                                from pathlib import Path

                                await self._session.send_realtime_input(
                                    video=types.Blob(data=Path(path).read_bytes(), mime_type="image/png")
                                )
                            except (OSError, ValueError):
                                pass
                        turn.tool_results.append(result)
                        responses.append(
                            types.FunctionResponse(id=str(call.id), name=name, response={"result": result.model_dump()})
                        )
                    await self._session.send_tool_response(function_responses=responses)
            # The Extended Thinking SDK iterator ends at turn_complete while
            # background reasoning continues; start the next receive iterator.
            if self.model != self.settings.gemini_extended_model:
                return turn

    async def text_turn(self, text: str, **kwargs: Any) -> LiveTurn:
        self.action_dispatched = False
        await self.send_text(text)
        return await self.receive_turn(**kwargs)
