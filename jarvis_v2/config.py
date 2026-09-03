from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def _text(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _integer(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(_text(name, str(default))))
    except ValueError:
        return default


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() not in {"0", "false", "no", "off"}


def _secret(name: str) -> str | None:
    value = _text(name)
    return value if value and value not in {"replace_me", "changeme"} else None


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    groq_api_key: str | None
    cerebras_api_key: str | None
    gemini_api_key: str | None
    openrouter_api_key: str | None
    mistral_api_key: str | None
    groq_model: str
    groq_compound_model: str
    cerebras_model: str
    gemini_model: str
    gemini_live_model: str
    openrouter_model: str | None
    mistral_model: str | None
    intent_provider_order: tuple[str, ...]
    conversation_provider_order: tuple[str, ...]
    pc_provider_order: tuple[str, ...]
    live_provider_order: tuple[str, ...]
    vision_provider_order: tuple[str, ...]
    connect_timeout_seconds: float
    request_timeout_seconds: float
    live_timeout_seconds: float
    circuit_failures: int
    circuit_cooldown_seconds: int
    max_tool_rounds: int
    voice_max_chars: int
    voice_max_sentences: int
    max_context_turns: int
    conversation_timeout_seconds: int
    custom_vocabulary: tuple[str, ...]
    memory_path: Path
    latency_log_path: Path
    tts_enabled: bool
    voice_input_enabled: bool

    @classmethod
    def from_env(cls, root: Path = ROOT) -> Settings:
        load_dotenv(root / ".env", override=False)

        def order(name: str, default: str) -> tuple[str, ...]:
            return tuple(item.strip().lower() for item in _text(name, default).split(",") if item.strip())

        vocabulary = tuple(
            item.strip()
            for item in _text(
                "JARVIS_CUSTOM_VOCABULARY",
                "Джарвіс,YouTube,YouTube Music,Steam,Discord,Telegram,Chrome,Visual Studio Code",
            ).split(",")
            if item.strip()
        )
        return cls(
            root=root,
            groq_api_key=_secret("GROQ_API_KEY"),
            cerebras_api_key=_secret("CEREBRAS_API_KEY"),
            gemini_api_key=_secret("GEMINI_API_KEY"),
            openrouter_api_key=_secret("OPENROUTER_API_KEY"),
            mistral_api_key=_secret("MISTRAL_API_KEY"),
            groq_model=_text("GROQ_MODEL", "openai/gpt-oss-120b"),
            groq_compound_model=_text("GROQ_COMPOUND_MODEL", "groq/compound-mini"),
            cerebras_model=_text("CEREBRAS_MODEL", "gpt-oss-120b"),
            gemini_model=_text("GEMINI_SMART_MODEL", "gemini-3.6-flash"),
            gemini_live_model=_text("GEMINI_LIVE_MODEL", "gemini-3.6-flash"),
            openrouter_model=_text("OPENROUTER_MODEL") or None,
            mistral_model=_text("MISTRAL_MODEL") or None,
            intent_provider_order=order("JARVIS_INTENT_PROVIDER_ORDER", "cerebras,groq,gemini"),
            conversation_provider_order=order(
                "JARVIS_CHAT_PROVIDER_ORDER", "gemini,groq,cerebras,openrouter,mistral"
            ),
            pc_provider_order=order("JARVIS_PC_PROVIDER_ORDER", "cerebras,groq,gemini,openrouter,mistral"),
            live_provider_order=order("JARVIS_LIVE_PROVIDER_ORDER", "gemini,groq_compound"),
            vision_provider_order=order("JARVIS_VISION_PROVIDER_ORDER", "gemini,openrouter"),
            connect_timeout_seconds=_integer("JARVIS_AI_CONNECT_TIMEOUT_MS", 1500, 100) / 1000,
            request_timeout_seconds=_integer("JARVIS_AI_READ_TIMEOUT_MS", 3500, 250) / 1000,
            live_timeout_seconds=_integer("JARVIS_LIVE_TIMEOUT_MS", 7000, 500) / 1000,
            circuit_failures=_integer("JARVIS_AI_CIRCUIT_FAILURES", 1, 1),
            circuit_cooldown_seconds=_integer("JARVIS_AI_TIMEOUT_COOLDOWN_SECS", 90, 1),
            max_tool_rounds=_integer("JARVIS_MAX_TOOL_ROUNDS", 6, 1),
            voice_max_chars=_integer("JARVIS_VOICE_MAX_CHARS", 280, 80),
            voice_max_sentences=_integer("JARVIS_VOICE_MAX_SENTENCES", 2, 1),
            max_context_turns=_integer("JARVIS_MAX_CONTEXT_TURNS", 8, 1),
            conversation_timeout_seconds=_integer("JARVIS_CONVERSATION_TIMEOUT_SECS", 60, 5),
            custom_vocabulary=vocabulary,
            memory_path=Path(_text("JARVIS_MEMORY_PATH", str(root / "data" / "jarvis-v2.db"))),
            latency_log_path=Path(_text("JARVIS_LATENCY_LOG", str(root / "logs" / "latency-v2.jsonl"))),
            tts_enabled=_boolean("JARVIS_TTS_ENABLED", True),
            voice_input_enabled=_boolean("JARVIS_VOICE_INPUT_ENABLED", True),
        )

    def configured_providers(self) -> set[str]:
        result: set[str] = set()
        if self.groq_api_key:
            result.update({"groq", "groq_compound"})
        if self.cerebras_api_key:
            result.add("cerebras")
        if self.gemini_api_key:
            result.add("gemini")
        if self.openrouter_api_key and self.openrouter_model:
            result.add("openrouter")
        if self.mistral_api_key and self.mistral_model:
            result.add("mistral")
        return result
