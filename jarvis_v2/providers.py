from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from jarvis_v2.config import Settings
from jarvis_v2.models import GroundingSource, IntentEnvelope, ProviderResponse, Route, ToolCall


@dataclass(slots=True)
class ProviderRequest:
    messages: list[dict[str, Any]]
    system: str
    route: Route
    tools: list[dict[str, Any]] = field(default_factory=list)
    response_schema: type[BaseModel] | None = None
    images: list[Path] = field(default_factory=list)
    max_tokens: int = 500


class ProviderFailure(RuntimeError):
    def __init__(self, provider: str, kind: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.provider = provider
        self.kind = kind
        self.safe_message = safe_message


class Provider(Protocol):
    name: str
    model: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...


def _value(obj: object, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)  # type: ignore[no-any-return]
    if hasattr(value, "to_dict"):
        return value.to_dict()  # type: ignore[no-any-return]
    return {key: item for key, item in vars(value).items() if not key.startswith("_")}


def _parse_tool_calls(message: object) -> list[ToolCall]:
    result: list[ToolCall] = []
    for index, call in enumerate(_value(message, "tool_calls", None) or []):
        function = _value(call, "function", {})
        arguments = _value(function, "arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"_invalid_json": arguments}
        result.append(
            ToolCall(
                id=str(_value(call, "id", f"call-{index}")),
                name=str(_value(function, "name", "")),
                arguments=arguments if isinstance(arguments, dict) else {"_invalid": arguments},
            )
        )
    return result


def _urls_from(value: object) -> list[GroundingSource]:
    found: dict[str, GroundingSource] = {}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            url = node.get("url") or node.get("uri")
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                title = node.get("title") or node.get("name") or url.split("/")[2]
                found[url] = GroundingSource(title=str(title), url=url)
            for child in node.values():
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)
        elif hasattr(node, "model_dump"):
            walk(node.model_dump(exclude_none=True))  # type: ignore[attr-defined]
        elif hasattr(node, "to_dict"):
            walk(node.to_dict())  # type: ignore[attr-defined]

    walk(value)
    return list(found.values())


def _messages(request: ProviderRequest) -> list[dict[str, Any]]:
    messages = [{"role": "system", "content": request.system}, *request.messages]
    result: list[dict[str, Any]] = []
    for message in messages:
        if not message.get("content") and not message.get("tool_calls"):
            continue
        current = dict(message)
        if current.get("role") == "assistant" and current.get("tool_calls"):
            current["tool_calls"] = [
                {
                    "id": call.get("id"),
                    "type": "function",
                    "function": {
                        "name": call.get("name"),
                        "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
                    },
                }
                for call in current["tool_calls"]
            ]
        result.append(current)
    return result


class GroqProvider:
    name = "groq"

    def __init__(self, key: str, model: str, timeout: float, *, compound: bool = False) -> None:
        self.key = key
        self.model = model
        self.timeout = timeout
        self.compound = compound
        self.name = "groq_compound" if compound else "groq"
        self._client: object | None = None

    def _get_client(self) -> object:
        if self._client is None:
            from groq import AsyncGroq

            self._client = AsyncGroq(api_key=self.key, timeout=self.timeout, max_retries=0)
        return self._client

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        client = self._get_client()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _messages(request),
            "max_completion_tokens": request.max_tokens,
            "temperature": 0 if request.route == Route.PC_AGENT else 0.3,
        }
        if request.tools and not self.compound:
            payload.update(tools=request.tools, tool_choice="auto")
        if self.compound:
            payload["citation_options"] = "enabled"
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema.__name__,
                    "strict": True,
                    "schema": request.response_schema.model_json_schema(),
                },
            }
        try:
            response = await client.chat.completions.create(**payload)  # type: ignore[attr-defined]
        except Exception as error:
            raise _sdk_failure(self.name, error) from error
        choice = response.choices[0]
        message = choice.message
        usage = _value(response, "usage")
        sources = _urls_from(_value(message, "executed_tools", []) or []) if self.compound else []
        return ProviderResponse(
            text=str(_value(message, "content", "") or ""),
            tool_calls=_parse_tool_calls(message),
            sources=sources,
            provider=self.name,
            model=self.model,
            input_tokens=_value(usage, "prompt_tokens"),
            output_tokens=_value(usage, "completion_tokens"),
            raw=response,
        )


class CerebrasProvider:
    name = "cerebras"

    def __init__(self, key: str, model: str, timeout: float) -> None:
        self.key = key
        self.model = model
        self.timeout = timeout
        self._client: object | None = None

    def _get_client(self) -> object:
        if self._client is None:
            from cerebras.cloud.sdk import Cerebras

            self._client = Cerebras(api_key=self.key, timeout=self.timeout, max_retries=0)
        return self._client

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _messages(request),
            "max_completion_tokens": request.max_tokens,
            "reasoning_effort": "low" if request.route == Route.PC_AGENT else "medium",
        }
        if request.tools:
            payload.update(tools=request.tools, tool_choice="auto")
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema.__name__,
                    "strict": True,
                    "schema": request.response_schema.model_json_schema(),
                },
            }
        try:
            response = await asyncio.to_thread(self._get_client().chat.completions.create, **payload)  # type: ignore[attr-defined]
        except Exception as error:
            raise _sdk_failure(self.name, error) from error
        message = response.choices[0].message
        usage = _value(response, "usage")
        return ProviderResponse(
            text=str(_value(message, "content", "") or ""),
            tool_calls=_parse_tool_calls(message),
            provider=self.name,
            model=self.model,
            input_tokens=_value(usage, "prompt_tokens"),
            output_tokens=_value(usage, "completion_tokens"),
            raw=response,
        )


class GeminiProvider:
    name = "gemini"

    def __init__(self, key: str, model: str, timeout: float = 7.0) -> None:
        self.key = key
        self.model = model
        self.timeout = timeout
        self._client: object | None = None

    def _get_client(self) -> object:
        if self._client is None:
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=self.key,
                http_options=types.HttpOptions(timeout=int(self.timeout * 1000)),
            )
        return self._client

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if request.route == Route.LIVE_CURRENT:
            try:
                return await self._interactions_live(request)
            except (AttributeError, TypeError):
                return await self._generate_content(request, search=True)
        return await self._generate_content(request, search=False)

    async def _interactions_live(self, request: ProviderRequest) -> ProviderResponse:
        client = self._get_client()
        text = "\n".join(
            str(message.get("content", "")) for message in request.messages if message.get("content")
        )
        interaction = await asyncio.to_thread(
            client.interactions.create,  # type: ignore[attr-defined]
            model=self.model,
            input=text,
            system_instruction=request.system,
            tools=[{"type": "google_search"}],
            timeout=self.timeout,
        )
        return ProviderResponse(
            text=str(_value(interaction, "output_text", "") or ""),
            sources=_urls_from(_value(interaction, "steps", []) or []),
            provider=self.name,
            model=self.model,
            raw=interaction,
        )

    async def _generate_content(self, request: ProviderRequest, *, search: bool) -> ProviderResponse:
        from google.genai import types

        client = self._get_client()
        config_kwargs: dict[str, Any] = {
            "system_instruction": request.system,
            "max_output_tokens": request.max_tokens,
        }
        if search:
            config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        elif request.tools:
            declarations = []
            for tool in request.tools:
                function = tool.get("function", {})
                declarations.append(
                    types.FunctionDeclaration(
                        name=str(function.get("name", "")),
                        description=str(function.get("description", "")),
                        parameters_json_schema=function.get("parameters", {}),
                    )
                )
            config_kwargs["tools"] = [types.Tool(function_declarations=declarations)]
        if request.response_schema is not None:
            config_kwargs.update(
                response_mime_type="application/json", response_schema=request.response_schema
            )
        contents: list[object] = [
            types.Content(
                role="user" if item.get("role") == "user" else "model",
                parts=[types.Part(text=str(item.get("content", "")))],
            )
            for item in request.messages
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        if request.images:
            parts: list[object] = [
                types.Part(text=str(request.messages[-1].get("content", "Проаналізуй екран.")))
            ]
            for path in request.images:
                parts.append(
                    types.Part.from_bytes(
                        data=path.read_bytes(),
                        mime_type=mimetypes.guess_type(path.name)[0] or "image/png",
                    )
                )
            contents = [types.Content(role="user", parts=parts)]
        try:
            response = await client.aio.models.generate_content(  # type: ignore[attr-defined]
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as error:
            raise _sdk_failure(self.name, error) from error
        usage = _value(response, "usage_metadata")
        function_calls = _value(response, "function_calls", []) or []
        return ProviderResponse(
            text=str(_value(response, "text", "") or ""),
            tool_calls=[
                ToolCall(
                    id=str(_value(call, "id", f"gemini-call-{index}")),
                    name=str(_value(call, "name", "")),
                    arguments=dict(_value(call, "args", {}) or {}),
                )
                for index, call in enumerate(function_calls)
            ],
            sources=_urls_from(_value(response, "candidates", []) or []),
            provider=self.name,
            model=self.model,
            input_tokens=_value(usage, "prompt_token_count"),
            output_tokens=_value(usage, "candidates_token_count"),
            raw=response,
        )


class MistralProvider:
    name = "mistral"

    def __init__(self, key: str, model: str, timeout: float = 3.5) -> None:
        self.key = key
        self.model = model
        self.timeout = timeout
        self._client: object | None = None

    def _get_client(self) -> object:
        if self._client is None:
            from mistralai.client import Mistral

            self._client = Mistral(api_key=self.key, timeout_ms=int(self.timeout * 1000))
        return self._client

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        messages = _messages(request)
        if request.images:
            content: list[dict[str, Any]] = [
                {"type": "text", "text": str(request.messages[-1].get("content", "Проаналізуй екран."))}
            ]
            for path in request.images:
                mime = mimetypes.guess_type(path.name)[0] or "image/png"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
            messages = [
                {"role": "system", "content": request.system},
                {"role": "user", "content": content},
            ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            payload["tools"] = request.tools
        try:
            response = await asyncio.to_thread(self._get_client().chat.complete, **payload)  # type: ignore[attr-defined]
        except Exception as error:
            raise _sdk_failure(self.name, error) from error
        message = response.choices[0].message
        usage = _value(response, "usage")
        return ProviderResponse(
            text=str(_value(message, "content", "") or ""),
            tool_calls=_parse_tool_calls(message),
            provider=self.name,
            model=self.model,
            input_tokens=_value(usage, "prompt_tokens"),
            output_tokens=_value(usage, "completion_tokens"),
            raw=response,
        )


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, key: str, model: str, timeout: float = 3.5) -> None:
        self.key = key
        self.model = model
        self.timeout = timeout
        self._client: object | None = None

    def _get_client(self) -> object:
        if self._client is None:
            from openrouter import OpenRouter

            self._client = OpenRouter(api_key=self.key, timeout_ms=int(self.timeout * 1000))
        return self._client

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        messages = _messages(request)
        if request.images:
            content: list[dict[str, Any]] = [
                {"type": "text", "text": str(request.messages[-1].get("content", "Проаналізуй екран."))}
            ]
            for path in request.images:
                mime = mimetypes.guess_type(path.name)[0] or "image/png"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
            messages = [
                {"role": "system", "content": request.system},
                {"role": "user", "content": content},
            ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            payload["tools"] = request.tools
        try:
            response = await asyncio.to_thread(self._get_client().chat.send, **payload)  # type: ignore[attr-defined]
        except Exception as error:
            raise _sdk_failure(self.name, error) from error
        message = response.choices[0].message
        usage = _value(response, "usage")
        return ProviderResponse(
            text=str(_value(message, "content", "") or ""),
            tool_calls=_parse_tool_calls(message),
            provider=self.name,
            model=self.model,
            input_tokens=_value(usage, "prompt_tokens"),
            output_tokens=_value(usage, "completion_tokens"),
            raw=response,
        )


def _sdk_failure(provider: str, error: Exception) -> ProviderFailure:
    name = type(error).__name__.lower()
    text = str(error).lower()
    if "timeout" in name or "timeout" in text or "timed out" in text:
        return ProviderFailure(provider, "timeout", f"{provider} не відповів вчасно.")
    if any(marker in text for marker in ("401", "403", "auth", "api key")):
        return ProviderFailure(provider, "auth", f"{provider} відхилив автентифікацію.")
    if "429" in text or ("rate" in text and "limit" in text):
        return ProviderFailure(provider, "rate_limit", f"{provider} тимчасово вичерпав квоту.")
    return ProviderFailure(provider, "provider", f"{provider} повернув помилку.")


@dataclass(slots=True)
class CircuitState:
    failures: int = 0
    disabled_until: float = 0
    auth_disabled: bool = False


class ProviderRouter:
    def __init__(self, settings: Settings, providers: Iterable[Provider] = ()) -> None:
        self.settings = settings
        self.providers = {provider.name: provider for provider in providers}
        self.health: dict[str, CircuitState] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> ProviderRouter:
        providers: list[Provider] = []
        if settings.groq_api_key:
            providers.extend(
                [
                    GroqProvider(
                        settings.groq_api_key, settings.groq_model, settings.request_timeout_seconds
                    ),
                    GroqProvider(
                        settings.groq_api_key,
                        settings.groq_compound_model,
                        settings.live_timeout_seconds,
                        compound=True,
                    ),
                ]
            )
        if settings.cerebras_api_key:
            providers.append(
                CerebrasProvider(
                    settings.cerebras_api_key, settings.cerebras_model, settings.request_timeout_seconds
                )
            )
        if settings.gemini_api_key:
            providers.append(
                GeminiProvider(settings.gemini_api_key, settings.gemini_model, settings.live_timeout_seconds)
            )
        if settings.mistral_api_key and settings.mistral_model:
            providers.append(
                MistralProvider(
                    settings.mistral_api_key, settings.mistral_model, settings.request_timeout_seconds
                )
            )
        if settings.openrouter_api_key and settings.openrouter_model:
            providers.append(
                OpenRouterProvider(
                    settings.openrouter_api_key,
                    settings.openrouter_model,
                    settings.request_timeout_seconds,
                )
            )
        return cls(settings, providers)

    def _chain(self, route: Route, *, structured: bool = False) -> tuple[str, ...]:
        if structured:
            return self.settings.intent_provider_order
        return {
            Route.CONVERSATION: self.settings.conversation_provider_order,
            Route.PC_AGENT: self.settings.pc_provider_order,
            Route.LIVE_CURRENT: self.settings.live_provider_order,
            Route.VISION: self.settings.vision_provider_order,
        }[route]

    async def complete(self, request: ProviderRequest, *, structured: bool = False) -> ProviderResponse:
        last_error: ProviderFailure | None = None
        names = self._chain(request.route, structured=structured)
        attempted = 0
        for name in names:
            provider = self.providers.get(name)
            state = self.health.setdefault(name, CircuitState())
            if provider is None or state.auth_disabled or state.disabled_until > time.monotonic():
                continue
            timeout = (
                self.settings.live_timeout_seconds
                if request.route == Route.LIVE_CURRENT
                else self.settings.request_timeout_seconds
            )
            try:
                response = await asyncio.wait_for(provider.complete(request), timeout=timeout)
            except TimeoutError:
                error = ProviderFailure(name, "timeout", f"{name} не відповів вчасно.")
            except ProviderFailure as caught:
                error = caught
            except Exception:
                error = ProviderFailure(name, "provider", f"{name} повернув помилку.")
            else:
                if request.route == Route.LIVE_CURRENT and not response.sources:
                    error = ProviderFailure(
                        name,
                        "ungrounded",
                        f"{name} не повернув перевірюваних джерел.",
                    )
                else:
                    state.failures = 0
                    state.disabled_until = 0
                    response.fallback_count = attempted
                    return response
            attempted += 1
            last_error = error
            state.failures += 1
            if error.kind == "auth":
                state.auth_disabled = True
            elif state.failures >= self.settings.circuit_failures:
                state.disabled_until = time.monotonic() + self.settings.circuit_cooldown_seconds
        if last_error:
            raise last_error
        raise ProviderFailure("router", "unconfigured", "Для цього маршруту не налаштовано AI-провайдера.")

    async def classify_intent(self, text: str, schema: type[IntentEnvelope]) -> object:
        prompt = (
            "Класифікуй лише Windows-дію користувача. Не домислюй пошкоджену команду. "
            "Для неоднозначної фрази поверни unknown, needs_clarification=true. "
            f"Фраза: {text}"
        )
        request = ProviderRequest(
            messages=[{"role": "user", "content": prompt}],
            system="Ти безпечний semantic intent classifier. Поверни тільки об'єкт за JSON Schema.",
            route=Route.PC_AGENT,
            response_schema=schema,
            max_tokens=350,
        )
        response = await self.complete(request, structured=True)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        return json.loads(raw)
