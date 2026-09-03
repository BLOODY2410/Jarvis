from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis_v2.models import Route
from jarvis_v2.providers import (
    CerebrasProvider,
    GeminiProvider,
    GroqProvider,
    MistralProvider,
    OpenRouterProvider,
    ProviderRequest,
    _messages,
)


def request(*, images: list[Path] | None = None) -> ProviderRequest:
    return ProviderRequest(
        messages=[{"role": "user", "content": "test"}],
        system="system",
        route=Route.CONVERSATION,
        images=images or [],
    )


def response(content: str = "ok") -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
    )


def test_internal_tool_messages_convert_to_sdk_contract() -> None:
    item = request()
    item.messages.extend(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "1", "name": "set_volume", "arguments": {"level": 50}}],
            },
            {"role": "tool", "tool_call_id": "1", "content": '{"success":true}'},
        ]
    )
    converted = _messages(item)
    tool_call = converted[2]["tool_calls"][0]
    assert tool_call["type"] == "function"
    assert tool_call["function"] == {"name": "set_volume", "arguments": '{"level": 50}'}


@pytest.mark.asyncio
async def test_groq_sdk_contract_is_mocked_without_network() -> None:
    captured = {}

    class Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return response()

    provider = GroqProvider("fake", "openai/gpt-oss-120b", 1)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = await provider.complete(request())
    assert result.text == "ok"
    assert captured["model"] == "openai/gpt-oss-120b"
    assert captured["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_cerebras_sdk_contract_is_mocked_without_network() -> None:
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return response()

    provider = CerebrasProvider("fake", "gpt-oss-120b", 1)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = await provider.complete(request())
    assert result.text == "ok"
    assert captured["reasoning_effort"] == "medium"


@pytest.mark.asyncio
async def test_mistral_sdk_contract_is_mocked_without_network() -> None:
    captured = {}

    class Chat:
        def complete(self, **kwargs):
            captured.update(kwargs)
            return response()

    provider = MistralProvider("fake", "mistral-small-latest")
    provider._client = SimpleNamespace(chat=Chat())
    result = await provider.complete(request())
    assert result.text == "ok"
    assert captured["max_tokens"] == 500


@pytest.mark.asyncio
async def test_openrouter_vision_contract_uses_data_url(tmp_path: Path) -> None:
    captured = {}
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake-png")

    class Chat:
        def send(self, **kwargs):
            captured.update(kwargs)
            return response()

    provider = OpenRouterProvider("fake", "openrouter/test")
    provider._client = SimpleNamespace(chat=Chat())
    result = await provider.complete(request(images=[image]))
    assert result.text == "ok"
    content = captured["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_gemini_interactions_provenance_contract() -> None:
    interaction = SimpleNamespace(
        output_text="grounded",
        steps=[
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "text",
                        "annotations": [
                            {"type": "url_citation", "title": "Official", "url": "https://example.com/fact"}
                        ],
                    }
                ],
            }
        ],
    )
    provider = GeminiProvider("fake", "gemini-3.6-flash")
    provider._client = SimpleNamespace(interactions=SimpleNamespace(create=lambda **_: interaction))
    item = request()
    item.route = Route.LIVE_CURRENT
    result = await provider.complete(item)
    assert result.text == "grounded"
    assert result.sources[0].url == "https://example.com/fact"
