from __future__ import annotations

import json
import statistics
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class LatencyTrace:
    utterance: str
    route: str = "unknown"
    provider: str | None = None
    started_ns: int = field(default_factory=time.perf_counter_ns)
    marks: dict[str, int] = field(default_factory=dict)

    def mark(self, name: str) -> None:
        self.marks[name] = (time.perf_counter_ns() - self.started_ns) // 1_000_000

    def payload(self) -> dict[str, object]:
        return {
            "timestamp_ms": int(time.time() * 1000),
            "utterance": self.utterance,
            "route": self.route,
            "provider": self.provider,
            "total_ms": (time.perf_counter_ns() - self.started_ns) // 1_000_000,
            **{f"{name}_ms": value for name, value in self.marks.items()},
        }


class LatencyLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, trace: LatencyTrace) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(trace.payload(), ensure_ascii=False) + "\n")


def percentile(samples: list[float], value: float) -> float:
    if not samples:
        return 0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * value)))
    return ordered[index]


def summarize(samples: list[float]) -> dict[str, float]:
    return {"median_ms": statistics.median(samples) if samples else 0, "p95_ms": percentile(samples, 0.95)}


@contextmanager
def measured(samples: list[float]) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        samples.append((time.perf_counter() - started) * 1000)
