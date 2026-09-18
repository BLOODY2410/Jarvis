from __future__ import annotations

import asyncio
import time
from pathlib import Path

from jarvis_v2.config import Settings
from jarvis_v2.intents import SemanticIntentRouter, deterministic_intent
from jarvis_v2.memory import MemoryStore, SessionState
from jarvis_v2.models import (
    AgentReply,
    IntentEnvelope,
    PersonalityMode,
    PowerIntent,
    Route,
    ToolResult,
    UnknownIntent,
)
from jarvis_v2.observability import LatencyLogger, LatencyTrace
from jarvis_v2.persona import Persona, infer_personality
from jarvis_v2.providers import ProviderFailure, ProviderRequest, ProviderRouter
from jarvis_v2.text import looks_like_action
from jarvis_v2.tools import ToolRegistry


class JarvisAgent:
    def __init__(
        self,
        settings: Settings,
        providers: ProviderRouter,
        tools: ToolRegistry,
        *,
        memory: MemoryStore | None = None,
        session: SessionState | None = None,
        persona: Persona | None = None,
        semantic: SemanticIntentRouter | None = None,
    ) -> None:
        self.settings = settings
        self.providers = providers
        self.tools = tools
        self.memory = memory
        self.session = session or SessionState()
        self.persona = persona or Persona(settings.voice_max_chars, settings.voice_max_sentences)
        self.semantic = semantic or SemanticIntentRouter(providers.classify_intent)
        self.latency = LatencyLogger(settings.latency_log_path)
        if self.memory:
            self.memory.start_session(self.session.session_id)

    async def handle(self, text: str, *, voice: bool = True) -> AgentReply:
        trace = LatencyTrace(text)
        self._remember_message("user", text)
        if self.session.pending_confirmation:
            pending = self.session.pending_confirmation
            if time.monotonic() - self.session.pending_confirmation_created_at > 30:
                self.session.pending_confirmation = None
                return self._reply(
                    "Час підтвердження минув. Дію скасовано.",
                    Route.PC_AGENT,
                    PersonalityMode.PC_AGENT,
                    voice=voice,
                )
            normalized = text.lower().strip(" .!?")
            if normalized in {"так", "підтверджую", "підтверди", "yes"}:
                self.session.pending_confirmation = None
                result = await asyncio.to_thread(self.tools.execute_intent, pending)
                return self._tool_reply([result], voice=voice)
            if normalized in {"ні", "скасуй", "відміна", "cancel", "no"}:
                self.session.pending_confirmation = None
                return self._reply("Дію скасовано.", Route.PC_AGENT, PersonalityMode.PC_AGENT, voice=voice)
            return self._reply("Очікую чітке підтвердження або скасування дії.", Route.PC_AGENT, PersonalityMode.PC_AGENT, voice=voice)
        envelope = deterministic_intent(text)
        trace.mark("intent")
        if envelope is None and looks_like_action(text):
            try:
                envelope = await self.semantic.classify(text)
            except ProviderFailure:
                envelope = None
            trace.mark("semantic_intent")

        if envelope is None:
            if looks_like_action(text):
                reply = self._reply(
                    "Не розібрав команду достатньо впевнено. Повторіть її коротше.",
                    Route.PC_AGENT,
                    PersonalityMode.PC_AGENT,
                    voice=voice,
                )
            else:
                reply = await self._conversation(text, voice=voice)
        elif envelope.needs_clarification:
            reply = self._reply(
                envelope.clarification or "Уточніть команду, будь ласка.",
                envelope.route,
                envelope.mode,
                voice=voice,
            )
        elif envelope.route == Route.LIVE_CURRENT:
            reply = await self._live(envelope, voice=voice)
        elif envelope.route == Route.VISION:
            reply = await self._vision(envelope, voice=voice)
        elif envelope.route == Route.PC_AGENT:
            reply = await self._pc(envelope, text, voice=voice)
        else:
            reply = await self._conversation(text, voice=voice)
        trace.route = reply.route.value
        trace.provider = reply.provider
        trace.mark("complete")
        self.latency.write(trace)
        self._remember_message("assistant", reply.text)
        return reply

    async def _pc(self, envelope: IntentEnvelope, text: str, *, voice: bool) -> AgentReply:
        power = next((intent for intent in envelope.intents if isinstance(intent, PowerIntent)), None)
        if power:
            self.session.pending_confirmation = power
            self.session.pending_confirmation_created_at = time.monotonic()
            labels = {"shutdown": "вимкнення", "restart": "перезавантаження", "sleep": "сон"}
            return self._reply(
                f"Підтвердьте {labels[power.action]} комп'ютера.",
                Route.PC_AGENT,
                PersonalityMode.PC_AGENT,
                voice=voice,
            )
        if envelope.intents:
            if any(isinstance(intent, UnknownIntent) for intent in envelope.intents):
                return self._reply(
                    "Не розібрав команду достатньо впевнено. Повторіть її коротше.",
                    Route.PC_AGENT,
                    PersonalityMode.PC_AGENT,
                    voice=voice,
                )
            results = [
                await asyncio.to_thread(self.tools.execute_intent, intent) for intent in envelope.intents
            ]
            return self._tool_reply(results, voice=voice)

        request = ProviderRequest(
            messages=self._context(text),
            system=self.persona.prompt(PersonalityMode.PC_AGENT),
            route=Route.PC_AGENT,
            tools=self.tools.schemas(),
            max_tokens=400,
        )
        all_results: list[ToolResult] = []
        for _ in range(self.settings.max_tool_rounds):
            try:
                response = await self.providers.complete(request)
            except ProviderFailure as error:
                return self._reply(error.safe_message, Route.PC_AGENT, PersonalityMode.PC_AGENT, voice=voice)
            if not response.tool_calls:
                # Model prose is not evidence that an action happened.
                if not all_results:
                    return self._reply(
                        "Команду не виконано: модель не надала перевіреного виклику інструмента.",
                        Route.PC_AGENT,
                        PersonalityMode.PC_AGENT,
                        voice=voice,
                    )
                return self._tool_reply(
                    all_results, voice=voice, provider=response.provider, model=response.model
                )
            request.messages.append(
                {
                    "role": "assistant",
                    "content": response.text or None,
                    "tool_calls": [call.model_dump() for call in response.tool_calls],
                }
            )
            for call in response.tool_calls:
                result = await asyncio.to_thread(self.tools.execute, call.name, call.arguments)
                all_results.append(result)
                request.messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result.model_dump_json()}
                )
        return self._tool_reply(all_results, voice=voice)

    async def _conversation(self, text: str, *, voice: bool) -> AgentReply:
        mode = infer_personality(text)
        request = ProviderRequest(
            messages=self._context(text),
            system=self.persona.prompt(mode),
            route=Route.CONVERSATION,
            max_tokens=500,
        )
        try:
            response = await self.providers.complete(request)
        except ProviderFailure as error:
            return self._reply(error.safe_message, Route.CONVERSATION, mode, voice=voice)
        if not response.text.strip():
            return self._reply("Не отримав змістовної відповіді.", Route.CONVERSATION, mode, voice=voice)
        return self._reply(
            response.text,
            Route.CONVERSATION,
            mode,
            voice=voice,
            provider=response.provider,
            model=response.model,
        )

    async def _live(self, envelope: IntentEnvelope, *, voice: bool) -> AgentReply:
        query = envelope.query or ""
        request = ProviderRequest(
            messages=self._context(query),
            system=self.persona.prompt(PersonalityMode.LIVE_INFO),
            route=Route.LIVE_CURRENT,
            max_tokens=650,
        )
        try:
            response = await self.providers.complete(request)
        except ProviderFailure:
            return self._reply(
                "Не вдалося отримати актуальні дані. Не стану вигадувати відповідь.",
                Route.LIVE_CURRENT,
                PersonalityMode.LIVE_INFO,
                voice=voice,
            )
        if not response.sources:
            return self._reply(
                "Отримав відповідь без перевірюваних джерел, тому не подаю її як актуальний факт.",
                Route.LIVE_CURRENT,
                PersonalityMode.LIVE_INFO,
                voice=voice,
                provider=response.provider,
                model=response.model,
            )
        self.session.grounded_facts.append(
            {
                "query": query,
                "answer": response.text,
                "sources": [source.model_dump() for source in response.sources],
            }
        )
        if self.memory:
            self.memory.grounded(self.session.session_id, query, response.text, response.sources)
        source_lines = "\n".join(f"- [{source.title}]({source.url})" for source in response.sources)
        full_text = f"{response.text.strip()}\n\nДжерела:\n{source_lines}"
        voice_text = self.persona.finish(response.text, voice=voice)
        return AgentReply(
            text=full_text,
            voice_text=voice_text,
            route=Route.LIVE_CURRENT,
            mode=PersonalityMode.LIVE_INFO,
            sources=response.sources,
            provider=response.provider,
            model=response.model,
        )

    async def _vision(self, envelope: IntentEnvelope, *, voice: bool) -> AgentReply:
        result = await asyncio.to_thread(self.tools.execute, "screenshot", {})
        self._remember_tool(result)
        if not result.success:
            return self._tool_reply([result], voice=voice)
        screenshot = Path(str(result.data["path"]))
        request = ProviderRequest(
            messages=[{"role": "user", "content": envelope.query or "Опиши, що зараз на екрані."}],
            system=self.persona.prompt(PersonalityMode.SERIOUS),
            route=Route.VISION,
            images=[screenshot],
            max_tokens=650,
        )
        try:
            response = await self.providers.complete(request)
        except ProviderFailure as error:
            return self._reply(
                f"Знімок зроблено, але аналіз не вдався: {error.safe_message}",
                Route.VISION,
                PersonalityMode.SERIOUS,
                voice=voice,
                screenshot=screenshot,
            )
        reply = self._reply(
            response.text,
            Route.VISION,
            PersonalityMode.SERIOUS,
            voice=voice,
            provider=response.provider,
            model=response.model,
            screenshot=screenshot,
        )
        reply.tool_results = [result]
        return reply

    def _tool_reply(
        self,
        results: list[ToolResult],
        *,
        voice: bool,
        provider: str | None = None,
        model: str | None = None,
    ) -> AgentReply:
        for result in results:
            self._remember_tool(result)
        if not results:
            text = "Команду не виконано: немає результату інструмента."
        else:
            text = " ".join(result.message for result in results)
        return self._reply(
            text,
            Route.PC_AGENT,
            PersonalityMode.ACTION if all(result.success for result in results) else PersonalityMode.PC_AGENT,
            voice=voice,
            tool_results=results,
            provider=provider,
            model=model,
        )

    def _reply(
        self,
        text: str,
        route: Route,
        mode: PersonalityMode,
        *,
        voice: bool,
        tool_results: list[ToolResult] | None = None,
        provider: str | None = None,
        model: str | None = None,
        screenshot: Path | None = None,
    ) -> AgentReply:
        return AgentReply(
            text=text.strip(),
            voice_text=self.persona.finish(text, voice=voice),
            route=route,
            mode=mode,
            tool_results=tool_results or [],
            provider=provider,
            model=model,
            screenshot=screenshot,
        )

    def _context(self, current: str) -> list[dict[str, str]]:
        context = list(self.session.messages)
        if not context or context[-1].get("content") != current:
            context.append({"role": "user", "content": current})
        return context

    def _remember_message(self, role: str, content: str) -> None:
        self.session.add_message(role, content, self.settings.max_context_turns)
        if self.memory:
            self.memory.message(self.session.session_id, role, content)

    def _remember_tool(self, result: ToolResult) -> None:
        if self.session.recent_tools and self.session.recent_tools[-1] == result:
            return
        self.session.recent_tools.append(result)
        if result.success:
            self.session.last_tool = result.tool
            if app := result.data.get("app"):
                self.session.last_app = str(app)
                self.session.last_entity = self.session.last_app
                self.session.recent_entities.append(self.session.last_app)
            if url := result.data.get("url"):
                self.session.last_url = str(url)
                self.session.last_entity = self.session.last_url
                self.session.recent_entities.append(self.session.last_url)
            if screen := result.data.get("path"):
                self.session.last_screen = str(screen)
                self.session.last_entity = self.session.last_screen
        if self.memory:
            self.memory.tool(self.session.session_id, result)

    def close(self) -> None:
        if self.memory:
            self.memory.end_session(self.session.session_id)
            self.memory.close()
            self.memory = None
