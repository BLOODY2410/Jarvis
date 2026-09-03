from __future__ import annotations

import asyncio

import pytest

from jarvis_v2.models import GroundingSource, ProviderResponse, Route
from jarvis_v2.providers import ProviderRequest, ProviderRouter


class SlowProvider:
    name = "gemini"
    model = "slow"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        await asyncio.sleep(1)
        return ProviderResponse(text="late", provider=self.name, model=self.model)


class FastProvider:
    name = "groq"
    model = "fast"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(text="fallback", provider=self.name, model=self.model)


class GroundedProvider:
    name = "gemini"
    model = "grounded"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            text="Підтверджена відповідь.",
            sources=[GroundingSource(title="Official", url="https://example.com/source")],
            provider=self.name,
            model=self.model,
        )


class UngroundedGemini:
    name = "gemini"
    model = "ungrounded"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(text="no citations", provider=self.name, model=self.model)


class GroundedCompound:
    name = "groq_compound"
    model = "groq/compound-mini"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            text="fallback with citations",
            sources=[GroundingSource(title="Official", url="https://example.com/live")],
            provider=self.name,
            model=self.model,
        )


@pytest.mark.asyncio
async def test_provider_timeout_falls_back(settings) -> None:
    router = ProviderRouter(settings, [SlowProvider(), FastProvider()])
    request = ProviderRequest(
        messages=[{"role": "user", "content": "hello"}],
        system="test",
        route=Route.CONVERSATION,
    )
    response = await router.complete(request)
    assert response.provider == "groq"
    assert response.fallback_count == 1
    assert router.health["gemini"].disabled_until > 0


@pytest.mark.asyncio
async def test_grounding_provenance_is_preserved(settings) -> None:
    router = ProviderRouter(settings, [GroundedProvider()])
    response = await router.complete(
        ProviderRequest(
            messages=[{"role": "user", "content": "news"}], system="test", route=Route.LIVE_CURRENT
        )
    )
    assert response.sources == [GroundingSource(title="Official", url="https://example.com/source")]


@pytest.mark.asyncio
async def test_ungrounded_live_response_falls_back(settings) -> None:
    router = ProviderRouter(settings, [UngroundedGemini(), GroundedCompound()])
    response = await router.complete(
        ProviderRequest(
            messages=[{"role": "user", "content": "news"}],
            system="test",
            route=Route.LIVE_CURRENT,
        )
    )
    assert response.provider == "groq_compound"
    assert response.fallback_count == 1
