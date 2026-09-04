from __future__ import annotations

import re
import unicodedata

WAKE_WORDS = {"джарвіс", "джарвис", "джарвиз", "jarvis"}
FILLERS = {"будь", "ласка", "можеш", "ну", "мені", "сер"}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower().replace("’", "'").replace("`", "'")
    value = re.sub(r"[^\wа-яіїєґё.'%:/+-]+", " ", value, flags=re.IGNORECASE)
    words = [word for word in value.strip(" .,!?;:").split() if word not in WAKE_WORDS | FILLERS]
    return " ".join(words)


def split_complete_sentences(value: str) -> list[str]:
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return []
    pieces = re.findall(r".+?(?:[.!?](?=\s|$)|$)", value)
    return [piece.strip() for piece in pieces if piece.strip()]


def voice_budget(value: str, max_chars: int = 280, max_sentences: int = 2) -> str:
    """Keep only complete sentences; never cut the last sentence mid-word."""
    sentences = split_complete_sentences(value)
    if not sentences:
        return ""
    selected: list[str] = []
    for sentence in sentences[:max_sentences]:
        candidate = " ".join([*selected, sentence])
        if selected and len(candidate) > max_chars:
            break
        selected.append(sentence)
    return " ".join(selected or sentences[:1]).strip()


def looks_like_action(value: str) -> bool:
    text = normalize_text(value)
    markers = (
        "відкрий",
        "відкрити",
        "запусти",
        "закрий",
        "увімкни",
        "включи",
        "вимкни",
        "постав",
        "зроби",
        "покажи",
        "перейди",
        "відкрой",
        "запусти",
        "закрой",
    )
    return text.startswith(markers)
