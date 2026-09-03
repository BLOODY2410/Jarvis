from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from jarvis_v2.agent import JarvisAgent
from jarvis_v2.benchmark import run as run_benchmark
from jarvis_v2.config import Settings
from jarvis_v2.memory import MemoryStore
from jarvis_v2.providers import ProviderRouter
from jarvis_v2.tools import ToolRegistry
from jarvis_v2.voice import InProcessVoiceRuntime


class ProcessLock:
    def __init__(self, root: Path) -> None:
        self.path = root / ".run" / "jarvis-v2.pid"

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                pid = int(self.path.read_text(encoding="ascii").strip())
                os.kill(pid, 0)
            except (ValueError, OSError):
                self.path.unlink(missing_ok=True)
            else:
                raise RuntimeError(f"JARVIS v2 already runs as PID {pid}")
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(str(os.getpid()), encoding="ascii")
        temporary.replace(self.path)

    def release(self) -> None:
        try:
            if self.path.exists() and self.path.read_text(encoding="ascii").strip() == str(os.getpid()):
                self.path.unlink()
        except OSError:
            pass


def build_agent(settings: Settings) -> JarvisAgent:
    memory = MemoryStore(settings.memory_path)
    return JarvisAgent(settings, ProviderRouter.from_settings(settings), ToolRegistry(), memory=memory)


async def run_text(agent: JarvisAgent, text: str) -> int:
    reply = await agent.handle(text, voice=False)
    print(reply.text)
    return 0


async def run_voice(agent: JarvisAgent, settings: Settings) -> int:
    runtime = InProcessVoiceRuntime(
        input_enabled=settings.voice_input_enabled,
        tts_enabled=settings.tts_enabled,
        custom_vocabulary=settings.custom_vocabulary,
    )
    lock = ProcessLock(settings.root)
    lock.acquire()
    try:
        await runtime.start()
        print("JARVIS v2 готовий. Скажіть «Джарвіс» або натисніть Ctrl+Alt+J. Ctrl+C — вихід.")
        conversation_active = False
        last_activity = time.monotonic()
        while True:
            event = await runtime.next_event()
            kind = event.get("type")
            if (
                conversation_active
                and time.monotonic() - last_activity >= settings.conversation_timeout_seconds
            ):
                runtime.set_state(False, False)
                conversation_active = False
            if kind == "wake":
                conversation_active = True
                last_activity = time.monotonic()
                await runtime.activation_cue()
                continue
            if kind == "interrupt":
                continue
            if kind != "transcript":
                continue
            text = str(event.get("text", "")).strip()
            if not text:
                continue
            conversation_active = True
            last_activity = time.monotonic()
            print(f"Ви: {text}")
            reply = await agent.handle(text, voice=True)
            print(f"JARVIS: {reply.text}")
            await runtime.speak(reply.voice_text)
    finally:
        await runtime.stop()
        lock.release()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JARVIS v2 Python-first desktop assistant")
    parser.add_argument("--text", help="Run one text turn without microphone or TTS")
    parser.add_argument("--benchmark", action="store_true", help="Benchmark deterministic intent routing")
    parser.add_argument("--doctor", action="store_true", help="Print safe configuration status")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    settings = Settings.from_env()
    if args.benchmark:
        print(json.dumps(run_benchmark(), ensure_ascii=False, indent=2))
        return 0
    if args.doctor:
        print(
            json.dumps(
                {
                    "python": "3.12",
                    "providers": sorted(settings.configured_providers()),
                    "voice_input": settings.voice_input_enabled,
                    "tts": settings.tts_enabled,
                    "localhost_sidecars": False,
                    "legacy_rust_preserved": (settings.root / "Cargo.toml").exists(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    agent = build_agent(settings)
    try:
        if args.text:
            return await run_text(agent, args.text)
        return await run_voice(agent, settings)
    finally:
        agent.close()


def main() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        print("JARVIS v2 зупинено.")


if __name__ == "__main__":
    main()
