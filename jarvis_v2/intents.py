from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from jarvis_v2.models import (
    AdjustVolumeIntent,
    CloseAppIntent,
    Intent,
    IntentEnvelope,
    ListAppsIntent,
    MediaIntent,
    OpenAppIntent,
    OpenUrlIntent,
    PersonalityMode,
    Route,
    ScreenshotIntent,
    SetMuteIntent,
    SetVolumeIntent,
    UnknownIntent,
    WindowsSettingsIntent,
)
from jarvis_v2.persona import infer_personality
from jarvis_v2.text import looks_like_action, normalize_text

WEBSITES = {
    "youtube": "https://www.youtube.com",
    "ютуб": "https://www.youtube.com",
    "google": "https://www.google.com",
    "гугл": "https://www.google.com",
    "github": "https://github.com",
    "гітхаб": "https://github.com",
    "telegram": "https://web.telegram.org",
    "телеграм": "https://web.telegram.org",
}
APPS = {
    "chrome": "chrome",
    "хром": "chrome",
    "google chrome": "chrome",
    "steam": "steam",
    "стім": "steam",
    "discord": "discord",
    "дискорд": "discord",
    "діскорд": "discord",
    "telegram": "telegram",
    "телеграм": "telegram",
    "visual studio code": "visual studio code",
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "калькулятор": "calculator",
    "calculator": "calculator",
    "блокнот": "notepad",
    "notepad": "notepad",
}
SETTINGS = {
    "налаштування": "settings",
    "настройки": "settings",
    "екран": "display",
    "дисплей": "display",
    "звук": "sound",
    "звуку": "sound",
    "мережа": "network",
    "мережі": "network",
    "сеть": "network",
    "інтернет": "network",
    "bluetooth": "bluetooth",
    "блютуз": "bluetooth",
    "програми": "apps",
    "приложения": "apps",
    "сповіщення": "notifications",
    "уведомления": "notifications",
    "приватність": "privacy",
    "конфіденційність": "privacy",
    "оновлення windows": "windows_update",
    "windows update": "windows_update",
}
NUMBER_WORDS = {
    "нуль": 0,
    "десять": 10,
    "двадцять": 20,
    "двадцать": 20,
    "тридцять": 30,
    "тридцать": 30,
    "сорок": 40,
    "п'ятдесят": 50,
    "пятдесят": 50,
    "пятьдесят": 50,
    "шістдесят": 60,
    "шестьдесят": 60,
    "сімдесят": 70,
    "семьдесят": 70,
    "вісімдесят": 80,
    "восемьдесят": 80,
    "дев'яносто": 90,
    "девяносто": 90,
    "сто": 100,
    "сотку": 100,
    "максимум": 100,
    "половину": 50,
}


def _envelope(text: str, intents: list[Intent], confidence: float = 1.0) -> IntentEnvelope:
    return IntentEnvelope(
        route=Route.PC_AGENT,
        mode=infer_personality(text, action=True),
        confidence=confidence,
        intents=intents,
    )


def _volume_level(text: str) -> int | None:
    if not any(marker in text for marker in ("гучн", "звук", "volume")):
        return None
    numeric = re.search(r"(?<!\d)(100|[1-9]?\d)\s*%?", text)
    if numeric:
        return int(numeric.group(1))
    return next((number for word, number in NUMBER_WORDS.items() if word in text), None)


def _single(text: str) -> IntentEnvelope | None:
    normalized = normalize_text(text)
    if not normalized:
        return None

    if normalized.startswith(("við grey ", "vid grey ", "vid grei ")):
        return IntentEnvelope(
            route=Route.PC_AGENT,
            mode=PersonalityMode.PC_AGENT,
            confidence=0.0,
            intents=[UnknownIntent(reason="пошкоджене дієслово STT")],
            needs_clarification=True,
            clarification="Не розібрав команду. Повторіть, будь ласка.",
        )

    if any(
        marker in normalized
        for marker in ("що на екрані", "шо на екрані", "подивись на екран", "проаналізуй екран")
    ):
        return IntentEnvelope(
            route=Route.VISION,
            mode=PersonalityMode.SERIOUS,
            confidence=1,
            intents=[ScreenshotIntent(analyze=True)],
            query=text,
        )

    if normalized in {"скріншот", "зроби скріншот", "знімок екрана", "зроби знімок екрана"}:
        return _envelope(text, [ScreenshotIntent()])

    live_markers = ("новин", "погод", "курс", "ціна", "рахунок", "хто виграв", "актуаль", "сьогодні", "зараз")
    if any(marker in normalized for marker in live_markers):
        return IntentEnvelope(
            route=Route.LIVE_CURRENT,
            mode=PersonalityMode.LIVE_INFO,
            confidence=0.98,
            query=text,
        )

    if normalized in {"що запущено", "які програми запущені", "покажи запущені програми"}:
        return _envelope(text, [ListAppsIntent()])

    if normalized in {"зроби голосніше", "голосніше", "додай гучність", "добавь звук"}:
        return _envelope(text, [AdjustVolumeIntent(delta=10)])
    if normalized in {"зроби тихіше", "тихіше", "зменш гучність", "убавь звук", "чуть потише"}:
        return _envelope(text, [AdjustVolumeIntent(delta=-10)])
    if any(marker == normalized for marker in ("вимкни звук", "без звуку", "заглуши звук")):
        return _envelope(text, [SetMuteIntent(muted=True)])
    if any(marker == normalized for marker in ("увімкни звук", "включи звук", "поверни звук")):
        return _envelope(text, [SetMuteIntent(muted=False)])
    level = _volume_level(normalized)
    if level is not None and any(
        marker in normalized for marker in ("постав", "встанов", "зроби", "гучність", "звук")
    ):
        return _envelope(text, [SetVolumeIntent(level=level)])

    if normalized in {"пауза", "постав на паузу", "продовж музику", "продовж відтворення"}:
        return _envelope(text, [MediaIntent(action="play_pause")])
    if normalized in {"наступний трек", "увімкни наступний трек"}:
        return _envelope(text, [MediaIntent(action="next")])
    if normalized in {"попередній трек", "увімкни попередній трек"}:
        return _envelope(text, [MediaIntent(action="previous")])

    setting_match = re.match(r"^(?:відкрий|відкрити|покажи|перейди в)\s+(.+)$", normalized)
    if setting_match:
        target = setting_match.group(1).removeprefix("налаштування ").removeprefix("настройки ").strip()
        if target in SETTINGS:
            return _envelope(text, [WindowsSettingsIntent(page=SETTINGS[target])])  # type: ignore[arg-type]

    open_match = re.match(r"^(?:відкрий|відкрити|запусти|увімкни|включи|відкрой)\s+(.+)$", normalized)
    if open_match:
        target = open_match.group(1).removeprefix("сайт ").strip()
        if target in WEBSITES:
            return _envelope(text, [OpenUrlIntent(url=WEBSITES[target])])
        if target in APPS:
            return _envelope(text, [OpenAppIntent(app=APPS[target])])
        if re.fullmatch(r"https?://[^\s]+", target):
            return _envelope(text, [OpenUrlIntent(url=target)])

    close_match = re.match(r"^(?:закрий|закрити|заверши|закрой)\s+(.+)$", normalized)
    if close_match:
        target = close_match.group(1).removeprefix("програму ").strip()
        if target in APPS:
            return _envelope(text, [CloseAppIntent(app=APPS[target])])
    return None


def deterministic_intent(text: str) -> IntentEnvelope | None:
    """Fast, safe local path. Ambiguous language is intentionally not guessed."""
    parts = re.split(
        r"\s+(?:а потім|після цього)\s+|\s+і\s+(?=(?:відкрий|закрий|постав|зроби|увімкни|вимкни))",
        text,
        flags=re.IGNORECASE,
    )
    if len(parts) == 1:
        return _single(text)
    envelopes = [_single(part) for part in parts]
    if any(item is None or item.route != Route.PC_AGENT or item.needs_clarification for item in envelopes):
        return None
    intents = [intent for envelope in envelopes if envelope for intent in envelope.intents]
    return _envelope(text, intents, min(envelope.confidence for envelope in envelopes if envelope))


class SemanticIntentRouter:
    """LLM-backed classifier whose output is accepted only after Pydantic validation."""

    def __init__(self, backend: Callable[[str, type[IntentEnvelope]], Awaitable[object]]) -> None:
        self._backend = backend

    async def classify(self, text: str) -> IntentEnvelope | None:
        if not looks_like_action(text):
            return None
        try:
            raw = await self._backend(text, IntentEnvelope)
            envelope = raw if isinstance(raw, IntentEnvelope) else IntentEnvelope.model_validate(raw)
        except (ValidationError, TypeError, ValueError):
            return None
        if envelope.confidence < 0.82 or not envelope.intents:
            return None
        return envelope
