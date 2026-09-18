from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis_v2.gemini_live import GeminiLiveSession
from jarvis_v2.tools import ToolRegistry
from tests_v2.test_tools import FakeBackend


class FakeLiveSession:
    def __init__(self) -> None:
        self.responses = [
            SimpleNamespace(
                server_content=None,
                tool_call=SimpleNamespace(
                    function_calls=[SimpleNamespace(id="set-1", name="set_volume", args={"level": 50})]
                ),
            ),
            SimpleNamespace(
                server_content=SimpleNamespace(
                    output_transcription=SimpleNamespace(text="Гучність встановлено."),
                    model_turn=None,
                    turn_complete=True,
                ),
                tool_call=None,
            ),
        ]
        self.tool_responses = []

    async def receive(self):
        for response in self.responses:
            yield response

    async def send_tool_response(self, *, function_responses):
        self.tool_responses.extend(function_responses)


@pytest.mark.asyncio
async def test_live_tool_loop_returns_a_real_validated_tool_result(settings) -> None:
    live = GeminiLiveSession(settings, ToolRegistry(FakeBackend()))
    live._session = FakeLiveSession()
    turn = await live.receive_turn()
    assert turn.text == "Гучність встановлено."
    assert len(turn.tool_results) == 1
    assert turn.tool_results[0].success
    assert turn.tool_results[0].data["level"] == 50


def test_live_declarations_are_accepted_by_the_installed_sdk(settings) -> None:
    from google.genai import types

    live = GeminiLiveSession(settings, ToolRegistry(FakeBackend()))
    declarations = live._function_declarations(live.tools.schemas())
    types.LiveConnectConfig(response_modalities=["AUDIO"], tools=[{"function_declarations": declarations}])


@pytest.mark.asyncio
async def test_close_ignores_sdk_cleanup_attribute_error(settings) -> None:
    class BrokenConnection:
        async def __aexit__(self, *_args: object) -> None:
            raise AttributeError("_async_httpx_client")

    live = GeminiLiveSession(settings, ToolRegistry(FakeBackend()))
    live._connection = BrokenConnection()
    await live.close()
    assert live._connection is None
