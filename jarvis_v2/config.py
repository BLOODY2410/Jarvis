from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(os.getenv("JARVIS_ROOT", Path(__file__).resolve().parents[1])).resolve()


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
    return value if value and value.lower() not in {"replace_me", "changeme"} else None


@dataclass(frozen=True, slots=True)
class Settings:
    """Small, explicit runtime configuration. Keys are optional and never logged."""

    root: Path
    gemini_api_key: str | None
    groq_api_key: str | None
    fish_api_key: str | None
    fish_voice_id: str | None
    gemini_live_model: str
    gemini_extended_model: str
    gemini_text_model: str
    groq_model: str
    voice_mode: str
    fallback_enabled: bool
    web_enabled: bool
    debug: bool
    request_timeout_seconds: float
    live_timeout_seconds: float
    circuit_failures: int
    circuit_cooldown_seconds: int
    max_tool_rounds: int
    voice_max_chars: int
    voice_max_sentences: int
    max_context_turns: int
    conversation_timeout_seconds: int
    wake_word: str
    wake_variants: tuple[str, ...]
    custom_vocabulary: tuple[str, ...]
    memory_path: Path
    latency_log_path: Path
    tts_enabled: bool
    voice_input_enabled: bool

    @classmethod
    def from_env(cls, root: Path = ROOT) -> Settings:
        load_dotenv(root / ".env", override=False)
        vocabulary = tuple(
            item.strip()
            for item in _text(
                "JARVIS_CUSTOM_VOCABULARY",
                "Джарвіс,YouTube,YouTube Music,Steam,Discord,Telegram,Chrome,Visual Studio Code",
            ).split(",")
            if item.strip()
        )
        wake_word = _text("JARVIS_WAKE_WORD", "джарвіс").lower()
        variants = tuple(
            dict.fromkeys(
                item.strip().lower()
                for item in _text("JARVIS_WAKE_VARIANTS", f"{wake_word},джарвис,джарвиз,jarvis").split(",")
                if item.strip()
            )
        )
        return cls(
            root=root,
            gemini_api_key=_secret("GEMINI_API_KEY"),
            groq_api_key=_secret("GROQ_API_KEY"),
            fish_api_key=_secret("FISH_API_KEY"),
            fish_voice_id=_secret("FISH_VOICE_ID"),
            gemini_live_model=_text("GEMINI_LIVE_MODEL", "gemini-3.8-live"),
            gemini_extended_model=_text("GEMINI_EXTENDED_MODEL", "gemini-3.8-live-extended-thinking"),
            gemini_text_model=_text("GEMINI_TEXT_MODEL", "gemini-3.8-flash"),
            groq_model=_text("GROQ_MODEL", "openai/gpt-oss-120b"),
            voice_mode=_text("JARVIS_VOICE_MODE", "gemini").lower(),
            fallback_enabled=_boolean("JARVIS_FALLBACK_ENABLED", True),
            # Live Google Search has a quota independent of the displayed Live
            # request/token limits. Keep it opt-in so a web quota issue cannot
            # prevent ordinary voice commands from starting.
            web_enabled=_boolean("JARVIS_WEB_ENABLED", False),
            debug=_boolean("JARVIS_DEBUG", False),
            request_timeout_seconds=_integer("JARVIS_REQUEST_TIMEOUT_MS", 3500, 250) / 1000,
            live_timeout_seconds=_integer("JARVIS_LIVE_TIMEOUT_MS", 7000, 500) / 1000,
            circuit_failures=_integer("JARVIS_CIRCUIT_FAILURES", 1, 1),
            circuit_cooldown_seconds=_integer("JARVIS_CIRCUIT_COOLDOWN_SECS", 90, 1),
            max_tool_rounds=_integer("JARVIS_MAX_TOOL_ROUNDS", 6, 1),
            voice_max_chars=_integer("JARVIS_VOICE_MAX_CHARS", 280, 80),
            voice_max_sentences=_integer("JARVIS_VOICE_MAX_SENTENCES", 2, 1),
            max_context_turns=_integer("JARVIS_MAX_CONTEXT_TURNS", 8, 1),
            conversation_timeout_seconds=_integer("JARVIS_CONVERSATION_TIMEOUT_SECS", 45, 5),
            wake_word=wake_word,
            wake_variants=variants,
            custom_vocabulary=vocabulary,
            memory_path=Path(_text("JARVIS_MEMORY_PATH", str(root / "data" / "jarvis-v2.db"))),
            latency_log_path=Path(_text("JARVIS_LATENCY_LOG", str(root / "logs" / "latency-v2.jsonl"))),
            tts_enabled=_boolean("JARVIS_TTS_ENABLED", True),
            voice_input_enabled=_boolean("JARVIS_VOICE_INPUT_ENABLED", True),
        )

    def configured_providers(self) -> set[str]:
        providers: set[str] = set()
        if self.gemini_api_key:
            providers.add("gemini")
        if self.fallback_enabled and self.groq_api_key:
            providers.add("groq")
        if self.fish_api_key:
            providers.add("fish")
        return providers
