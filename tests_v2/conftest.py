from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_v2.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    base = Settings.from_env()
    return replace(
        base,
        memory_path=tmp_path / "memory.db",
        latency_log_path=tmp_path / "latency.jsonl",
        request_timeout_seconds=0.05,
        live_timeout_seconds=0.05,
        circuit_failures=1,
        circuit_cooldown_seconds=60,
        intent_provider_order=("cerebras", "groq"),
        conversation_provider_order=("gemini", "groq"),
        pc_provider_order=("cerebras", "groq"),
        live_provider_order=("gemini", "groq_compound"),
        vision_provider_order=("gemini",),
    )
