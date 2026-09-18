from __future__ import annotations

import sqlite3

import pytest

from jarvis_v2.agent import JarvisAgent
from jarvis_v2.memory import MemoryStore
from jarvis_v2.models import GroundingSource, ProviderResponse, Route
from jarvis_v2.providers import ProviderRequest, ProviderRouter
from jarvis_v2.tools import ToolRegistry
from tests_v2.test_tools import FakeBackend


class LiveProvider:
    name = "gemini"
    model = "live"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            text="Актуальна відповідь.",
            sources=[GroundingSource(title="Source", url="https://example.com")],
            provider=self.name,
            model=self.model,
        )


class UngroundedProvider:
    name = "gemini"
    model = "live"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(text="Trust me", provider=self.name, model=self.model)


class FakeSuccessProvider:
    name = "gemini"
    model = "pc"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(text="YouTube відкрито.", provider=self.name, model=self.model)


@pytest.mark.asyncio
async def test_live_answer_is_saved_only_with_provenance(settings) -> None:
    store = MemoryStore(settings.memory_path)
    agent = JarvisAgent(
        settings,
        ProviderRouter(settings, [LiveProvider()]),
        ToolRegistry(FakeBackend()),
        memory=store,
    )
    reply = await agent.handle("Що там у новинах сьогодні?", voice=False)
    assert reply.route == Route.LIVE_CURRENT
    assert reply.sources
    count = sqlite3.connect(settings.memory_path).execute("SELECT count(*) FROM grounded_event").fetchone()[0]
    assert count == 1
    sessions = sqlite3.connect(settings.memory_path).execute("SELECT count(*) FROM session").fetchone()[0]
    assert sessions == 1


@pytest.mark.asyncio
async def test_ungrounded_live_answer_is_not_presented_as_fact(settings) -> None:
    agent = JarvisAgent(
        settings,
        ProviderRouter(settings, [UngroundedProvider()]),
        ToolRegistry(FakeBackend()),
    )
    reply = await agent.handle("Відкрий новини", voice=False)
    assert "Не вдалося отримати актуальні дані" in reply.text
    assert "Trust me" not in reply.text


@pytest.mark.asyncio
async def test_model_cannot_fake_action_success(settings) -> None:
    agent = JarvisAgent(
        settings,
        ProviderRouter(settings, [FakeSuccessProvider()]),
        ToolRegistry(FakeBackend()),
    )
    from jarvis_v2.models import IntentEnvelope, PersonalityMode

    envelope = IntentEnvelope(route=Route.PC_AGENT, mode=PersonalityMode.PC_AGENT, confidence=1, intents=[])
    reply = await agent._pc(envelope, "відкрий щось", voice=False)
    assert "не виконано" in reply.text.lower()
    assert "YouTube відкрито" not in reply.text


@pytest.mark.asyncio
async def test_multi_intent_executes_in_order(settings) -> None:
    agent = JarvisAgent(settings, ProviderRouter(settings, []), ToolRegistry(FakeBackend()))
    reply = await agent.handle("відкрий Chrome а потім постав гучність на 30", voice=False)
    assert [result.tool for result in reply.tool_results] == ["open_app", "set_volume"]
    assert all(result.success for result in reply.tool_results)


@pytest.mark.asyncio
async def test_power_action_needs_explicit_confirmation(settings) -> None:
    agent = JarvisAgent(settings, ProviderRouter(settings, []), ToolRegistry(FakeBackend()))
    waiting = await agent.handle("вимкни комп'ютер", voice=False)
    assert "Підтвердьте" in waiting.text
    assert not waiting.tool_results
    completed = await agent.handle("підтверджую", voice=False)
    assert completed.tool_results[0].tool == "power"
    assert completed.tool_results[0].success
