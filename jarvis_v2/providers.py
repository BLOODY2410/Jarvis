from __future__ import annotations

import asyncio
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
        self.provider, self.kind, self.safe_message = provider, kind, safe_message


class Provider(Protocol):
    name: str
    model: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...


def _value(obj: object, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _urls_from(value: object) -> list[GroundingSource]:
    found: dict[str, GroundingSource] = {}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            url = node.get("url") or node.get("uri")
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                found[url] = GroundingSource(title=str(node.get("title") or url.split("/")[2]), url=url)
            for child in node.values():
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)
        elif hasattr(node, "model_dump"):
            walk(node.model_dump(exclude_none=True))  # type: ignore[attr-defined]

    walk(value)
    return list(found.values())


def _tool_calls(value: object) -> list[ToolCall]:
    return [
        ToolCall(
            id=str(_value(call, "id", f"gemini-{index}")),
            name=str(_value(call, "name", "")),
            arguments=dict(_value(call, "args", {}) or {}),
        )
        for index, call in enumerate(_value(value, "function_calls", []) or [])
    ]


class GeminiProvider:
    """Text/vision companion for Live sessions, implemented with Google's current SDK."""

    name = "gemini"

    def __init__(self, key: str, model: str, timeout: float = 3.5) -> None:
        self.key, self.model, self.timeout = key, model, timeout
        self._client: object | None = None

    def _get_client(self) -> object:
        if self._client is None:
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=self.key, http_options=types.HttpOptions(timeout=int(self.timeout * 1000))
            )
        return self._client

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        from google.genai import types

        config: dict[str, Any] = {"system_instruction": request.system, "max_output_tokens": request.max_tokens}
        if request.route == Route.LIVE_CURRENT:
            config["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        elif request.tools:
            config["tools"] = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=str(item["function"]["name"]),
                            description=str(item["function"]["description"]),
                            parameters_json_schema=item["function"]["parameters"],
                        )
                        for item in request.tools
                    ]
                )
            ]
        if request.response_schema:
            config.update(response_mime_type="application/json", response_schema=request.response_schema)
        contents: list[object] = []
        tool_names: dict[str, str] = {}
        for message in request.messages:
            role = message.get("role")
            if role not in {"user", "assistant", "tool"}:
                continue
            parts: list[object] = []
            if message.get("content") and role != "tool":
                parts.append(types.Part(text=str(message["content"])))
            if role == "assistant":
                for call in message.get("tool_calls", []):
                    call_id = str(call.get("id", ""))
                    name = str(call.get("name", ""))
                    tool_names[call_id] = name
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                id=call_id, name=name, args=dict(call.get("arguments", {}) or {})
                            )
                        )
                    )
            if role == "tool":
                call_id = str(message.get("tool_call_id", ""))
                try:
                    result = json.loads(str(message.get("content", "{}")))
                except json.JSONDecodeError:
                    result = {"success": False, "error": "invalid local tool receipt"}
                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=call_id,
                            name=tool_names.get(call_id, "unknown_tool"),
                            response={"result": result},
                        )
                    )
                )
            if parts:
                contents.append(types.Content(role="model" if role == "assistant" else "user", parts=parts))
        if request.images:
            parts: list[object] = [types.Part(text=str(request.messages[-1].get("content", "Опиши екран.")))]
            for path in request.images:
                parts.append(
                    types.Part.from_bytes(
                        data=path.read_bytes(), mime_type=mimetypes.guess_type(path.name)[0] or "image/png"
                    )
                )
            contents = [types.Content(role="user", parts=parts)]
        try:
            response = await self._get_client().aio.models.generate_content(  # type: ignore[attr-defined]
                model=self.model, contents=contents, config=types.GenerateContentConfig(**config)
            )
        except Exception as error:
            raise _sdk_failure(self.name, error) from error
        usage = _value(response, "usage_metadata")
        return ProviderResponse(
            text=str(_value(response, "text", "") or ""),
            tool_calls=_tool_calls(response),
            sources=_urls_from(_value(response, "candidates", []) or []),
            provider=self.name,
            model=self.model,
            input_tokens=_value(usage, "prompt_token_count"),
            output_tokens=_value(usage, "candidates_token_count"),
            raw=response,
        )


class GroqProvider:
    """Emergency text fallback only; it never competes with Gemini as the main brain."""

    name = "groq"

    def __init__(self, key: str, model: str, timeout: float) -> None:
        self.key, self.model, self.timeout = key, model, timeout
        self._client: object | None = None

    def _get_client(self) -> object:
        if self._client is None:
            from groq import AsyncGroq

            self._client = AsyncGroq(api_key=self.key, timeout=self.timeout, max_retries=0)
        return self._client

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": request.system}, *request.messages],
            "max_completion_tokens": request.max_tokens,
            "temperature": 0 if request.route == Route.PC_AGENT else 0.3,
        }
        if request.tools:
            payload.update(tools=request.tools, tool_choice="auto")
        if request.response_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema.__name__,
                    "strict": True,
                    "schema": request.response_schema.model_json_schema(),
                },
            }
        try:
            response = await self._get_client().chat.completions.create(**payload)  # type: ignore[attr-defined]
        except Exception as error:
            raise _sdk_failure(self.name, error) from error
        message = response.choices[0].message
        calls: list[ToolCall] = []
        for index, call in enumerate(getattr(message, "tool_calls", None) or []):
            try:
                arguments = json.loads(call.function.arguments)
            except (TypeError, json.JSONDecodeError):
                arguments = {"_invalid_json": call.function.arguments}
            calls.append(ToolCall(id=call.id or f"groq-{index}", name=call.function.name, arguments=arguments))
        return ProviderResponse(text=str(message.content or ""), tool_calls=calls, provider=self.name, model=self.model, raw=response)


def _sdk_failure(provider: str, error: Exception) -> ProviderFailure:
    value = f"{type(error).__name__} {error}".lower()
    if "timeout" in value or "timed out" in value:
        return ProviderFailure(provider, "timeout", f"{provider} не відповів вчасно.")
    if any(marker in value for marker in ("401", "403", "auth", "api key")):
        return ProviderFailure(provider, "auth", f"{provider} відхилив автентифікацію.")
    if "429" in value or "rate limit" in value:
        return ProviderFailure(provider, "rate_limit", f"{provider} тимчасово вичерпав квоту.")
    return ProviderFailure(provider, "provider", f"{provider} повернув помилку.")


@dataclass(slots=True)
class CircuitState:
    failures: int = 0
    disabled_until: float = 0
    auth_disabled: bool = False


class ProviderRouter:
    """Gemini first, with one optional Groq emergency fallback and a circuit breaker."""

    def __init__(self, settings: Settings, providers: Iterable[Provider] = ()) -> None:
        self.settings = settings
        self.providers = {provider.name: provider for provider in providers}
        self.health: dict[str, CircuitState] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> ProviderRouter:
        providers: list[Provider] = []
        if settings.gemini_api_key:
            providers.append(GeminiProvider(settings.gemini_api_key, settings.gemini_text_model, settings.request_timeout_seconds))
        if settings.fallback_enabled and settings.groq_api_key:
            providers.append(GroqProvider(settings.groq_api_key, settings.groq_model, settings.request_timeout_seconds))
        return cls(settings, providers)

    async def complete(self, request: ProviderRequest, *, structured: bool = False) -> ProviderResponse:
        del structured
        last_error: ProviderFailure | None = None
        attempted = 0
        names = ("gemini", "groq") if self.settings.fallback_enabled else ("gemini",)
        for name in names:
            provider = self.providers.get(name)
            state = self.health.setdefault(name, CircuitState())
            if provider is None or state.auth_disabled or state.disabled_until > time.monotonic():
                continue
            try:
                response = await asyncio.wait_for(provider.complete(request), timeout=self.settings.request_timeout_seconds)
                if request.route == Route.LIVE_CURRENT and not response.sources:
                    raise ProviderFailure(name, "ungrounded", f"{name} не повернув перевірюваних джерел.")
            except TimeoutError:
                error = ProviderFailure(name, "timeout", f"{name} не відповів вчасно.")
            except ProviderFailure as caught:
                error = caught
            except Exception:
                error = ProviderFailure(name, "provider", f"{name} повернув помилку.")
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
        raise ProviderFailure("router", "unconfigured", "Gemini не налаштовано; fallback теж недоступний.")

    async def classify_intent(self, text: str, schema: type[IntentEnvelope]) -> object:
        request = ProviderRequest(
            messages=[{"role": "user", "content": f"Класифікуй дію без домислів: {text}"}],
            system="Поверни лише JSON за schema. Не виконуй дій і не вгадуй пошкоджені слова.",
            route=Route.PC_AGENT,
            response_schema=schema,
            max_tokens=350,
        )
        response = await self.complete(request, structured=True)
        return json.loads(response.text.strip().removeprefix("```json").removesuffix("```").strip())
