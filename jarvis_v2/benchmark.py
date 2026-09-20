from __future__ import annotations

from time import perf_counter

from jarvis_v2.intents import deterministic_intent
from jarvis_v2.observability import summarize

CASES = [
    "Відкрий YouTube",
    "поставь звук на п'ятдесят",
    "зроби тихіше",
    "відкрий налаштування звуку",
    "Відкрий новини",
    "Við grey YouTube",
    "зроби скріншот",
    "відкрий Chrome а потім постав гучність на 30",
]


def run(iterations: int = 1000) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(iterations):
        for text in CASES:
            started = perf_counter()
            deterministic_intent(text)
            samples.append((perf_counter() - started) * 1000)
    return summarize(samples)
