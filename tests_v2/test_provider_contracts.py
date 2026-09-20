from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis_v2.models import Route
from jarvis_v2.providers import GeminiProvider, ProviderRequest


@pytest.mark.asyncio
async def test_gemini_sdk_contract_is_mocked_without_network() -> None:
    provider = GeminiProvider("fake", "gemini-3.8-flash")
    captured: dict[str, object] = {}

    class Models:
        async def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text="ok", function_calls=[], candidates=[], usage_metadata=None)

    provider._client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    request = ProviderRequest(
        messages=[{"role": "user", "content": "тест"}], system="system", route=Route.CONVERSATION
    )
    result = await provider.complete(request)
    assert result.text == "ok"
    assert captured["model"] == "gemini-3.8-flash"


@pytest.mark.asyncio
async def test_gemini_tool_receipts_stay_in_followup_context() -> None:
    provider = GeminiProvider("fake", "gemini-3.8-flash")
    captured: dict[str, object] = {}

    class Models:
        async def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text="done", function_calls=[], candidates=[], usage_metadata=None)

    provider._client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    request = ProviderRequest(
        messages=[
            {"role": "user", "content": "постав звук"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "name": "set_volume", "arguments": {"level": 50}}]},
            {"role": "tool", "tool_call_id": "1", "content": '{"success":true}'},
        ],
        system="system",
        route=Route.PC_AGENT,
    )
    await provider.complete(request)
    contents = captured["contents"]
    assert contents[1].parts[0].function_call.name == "set_volume"
    assert contents[2].parts[0].function_response.name == "set_volume"
