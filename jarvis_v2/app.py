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
from jarvis_v2.gemini_live import GeminiLiveSession
from jarvis_v2.memory import MemoryStore
from jarvis_v2.models import PersonalityMode
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
    runtime = InProcessVoiceRuntime(settings)
    lock = ProcessLock(settings.root)
    lock.acquire()
    live: GeminiLiveSession | None = None
    try:
        await runtime.start()
        print("JARVIS v2")
        print(f"Gemini 3.8 Live: {'available' if settings.gemini_api_key else 'not configured'}")
        print(f"Extended Thinking: {'available' if settings.gemini_api_key else 'not configured'}")
        print(f"Groq fallback: {'available' if settings.groq_api_key and settings.fallback_enabled else 'not configured'}")
        print("JARVIS online. Скажіть «Джарвіс» або натисніть Ctrl+Alt+J. Ctrl+C — вихід.")
        conversation_active = False
        last_activity = time.monotonic()
        while True:
            event = await runtime.next_event()
            kind = event.kind
            if (
                conversation_active
                and time.monotonic() - last_activity >= settings.conversation_timeout_seconds
            ):
                runtime.deactivate()
                conversation_active = False
                if live:
                    await live.close()
                    live = None
            if kind == "wake":
                conversation_active = True
                last_activity = time.monotonic()
                await runtime.activation_cue()
                continue
            if kind == "interrupt":
                runtime.interrupt_playback()
                continue
            if kind != "audio":
                continue
            conversation_active = True
            last_activity = time.monotonic()
            if settings.voice_mode == "gemini" and settings.gemini_api_key:
                try:
                    if live is None:
                        live = GeminiLiveSession(settings, agent.tools)
                        await live.connect(agent.persona.prompt(PersonalityMode.CASUAL))
                    live.action_dispatched = False
                    await live.send_audio(event.audio)
                    response = await live.receive_turn()
                    if response.text:
                        print(f"JARVIS: {response.text}")
                    await runtime.play_pcm(response.audio_pcm)
                    continue
                except Exception as error:
                    if settings.debug:
                        print(f"[Gemini] Live unavailable: {type(error).__name__}")
                    action_dispatched = bool(live and live.action_dispatched)
                    if live:
                        await live.close()
                    live = None
                    if action_dispatched:
                        print("JARVIS: Зв'язок обірвався після запуску дії. Її стан невідомий; повторно не запускаю.")
                        continue
            text = await runtime.transcribe_fallback(event.audio)
            if not text:
                print("JARVIS: Не почув команду. Повторіть, будь ласка.")
                continue
            print(f"Ви: {text}")
            reply = await agent.handle(text, voice=True)
            print(f"JARVIS: {reply.text}")
            await runtime.speak_fallback(reply.voice_text)
    finally:
        if live:
            await live.close()
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
                    "voice_mode": settings.voice_mode,
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
