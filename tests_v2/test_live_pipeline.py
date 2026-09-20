from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis_v2.agent import JarvisAgent
from jarvis_v2.gemini_live import GeminiLiveSession
from jarvis_v2.memory import MemoryStore
from jarvis_v2.providers import ProviderRouter
from jarvis_v2.tools import ToolRegistry
from tests_v2.test_tools import FakeBackend


class StreamingSession:
    async def receive(self):
        yield SimpleNamespace(
            server_content=SimpleNamespace(
                input_transcription=SimpleNamespace(text="відкрий YouTube"),
                output_transcription=None,
                model_turn=SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b"pcm"), text=None)]),
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[SimpleNamespace(web=SimpleNamespace(uri="https://example.com", title="Example"))]
                ),
                turn_complete=False,
                interaction_status="IN_PROGRESS",
            ),
            tool_call=None,
        )
        yield SimpleNamespace(
            server_content=SimpleNamespace(
                input_transcription=None, output_transcription=SimpleNamespace(text="Готово."), model_turn=None,
                grounding_metadata=None, turn_complete=True, interaction_status="IDLE",
            ),
            tool_call=None,
        )


@pytest.mark.asyncio
async def test_live_streams_chunk_before_turn_complete_and_parses_real_grounding(settings) -> None:
    live = GeminiLiveSession(settings, ToolRegistry(FakeBackend()))
    live._session = StreamingSession()
    chunks: list[bytes] = []

    async def on_audio(chunk: bytes) -> None:
        chunks.append(chunk)

    turn = await live.receive_turn(on_audio=on_audio)
    assert chunks == [b"pcm"]
    assert turn.user_text == "відкрий YouTube"
    assert turn.sources[0].url == "https://example.com"


@pytest.mark.asyncio
async def test_primary_live_input_transcript_can_stop_for_host_fast_path(settings) -> None:
    live = GeminiLiveSession(settings, ToolRegistry(FakeBackend()))
    live._session = StreamingSession()
    seen: list[str] = []

    async def preflight(text: str) -> bool:
        seen.append(text)
        return True

    turn = await live.receive_turn(on_input_transcript=preflight)
    assert turn.fast_handled
    assert seen == ["відкрий YouTube"]


@pytest.mark.asyncio
async def test_live_receipt_updates_shared_session_memory_once(settings) -> None:
    store = MemoryStore(settings.memory_path)
    agent = JarvisAgent(settings, ProviderRouter(settings, []), ToolRegistry(FakeBackend()), memory=store)
    result = await agent.execute_live_tool("open_app", {"app": "steam"})
    assert result.success
    assert agent.session.last_app == "steam"
    assert agent.session.last_tool == "open_app"


@pytest.mark.asyncio
async def test_copy_confirmation_keeps_canonical_arguments_server_side(settings) -> None:
    agent = JarvisAgent(settings, ProviderRouter(settings, []), ToolRegistry(FakeBackend()))
    pending = await agent.execute_live_tool("copy_file", {"source": "bad", "destination": "also-bad"})
    assert not pending.success
    assert agent.session.pending_confirmation is not None
    reply = await agent.handle("скасуй", voice=False)
    assert "скасовано" in reply.text
    assert agent.session.pending_confirmation is None


def test_dynamic_persona_and_narrow_extended_gate(settings) -> None:
    agent = JarvisAgent(settings, ProviderRouter(settings, []), ToolRegistry(FakeBackend()))
    assert "режим BANTER" in agent.live_guidance("ти тупий")
    assert agent.needs_extended_thinking("спочатку проаналізуй екран, потім виправ помилку якщо знайдеш")
    assert not agent.needs_extended_thinking("відкрий YouTube")
