from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import time
from contextlib import suppress
from pathlib import Path

from jarvis_v2.agent import JarvisAgent
from jarvis_v2.benchmark import run as run_benchmark
from jarvis_v2.config import Settings
from jarvis_v2.gemini_live import GeminiLiveSession
from jarvis_v2.memory import MemoryStore
from jarvis_v2.models import PersonalityMode
from jarvis_v2.processes import (
    RUNTIME_MUTEX,
    clear_runtime_error,
    clear_runtime_state,
    write_runtime_error,
    write_runtime_state,
)
from jarvis_v2.providers import ProviderRouter
from jarvis_v2.tools import ToolRegistry
from jarvis_v2.voice import InProcessVoiceRuntime


class ProcessLock:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._mutex: int | None = None

    def _acquire_windows_mutex(self) -> None:
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, RUNTIME_MUTEX)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            raise RuntimeError("JARVIS v2 is already running.")
        self._mutex = int(handle)

    def acquire(self) -> None:
        self._acquire_windows_mutex()
        try:
            clear_runtime_state()
            write_runtime_state(os.getpid(), self.root)
        except Exception:
            self._release_windows_mutex()
            raise

    def _release_windows_mutex(self) -> None:
        if self._mutex is not None:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._mutex)
            self._mutex = None

    def release(self) -> None:
        clear_runtime_state(os.getpid())
        self._release_windows_mutex()


def build_agent(settings: Settings) -> JarvisAgent:
    memory = MemoryStore(settings.memory_path)
    return JarvisAgent(settings, ProviderRouter.from_settings(settings), ToolRegistry(), memory=memory)


def _live_error_message(error: Exception) -> str:
    detail = str(error).lower()
    if "quota" in detail or "exceeded" in detail:
        return "Gemini API відхилив команду: для цього ключа вичерпано доступну квоту."
    if "api key" in detail or "unauthenticated" in detail or "permission" in detail:
        return "Gemini API відхилив ключ. Перевірте API key та доступ до Gemini Live."
    return f"Gemini Live тимчасово недоступний ({type(error).__name__})."


async def run_text(agent: JarvisAgent, text: str) -> int:
    reply = await agent.handle(text, voice=False)
    print(reply.text)
    return 0


async def _receive_with_barge_in(runtime: InProcessVoiceRuntime, receive: asyncio.Future) -> tuple[object | None, str | None]:
    """Cancel a Live receive on local interruption without replaying an action."""
    event_task = asyncio.create_task(runtime.next_event())
    try:
        while True:
            done, _ = await asyncio.wait({receive, event_task}, return_when=asyncio.FIRST_COMPLETED)
            if receive in done:
                return receive.result(), None
            event = event_task.result()
            if event.kind in {"interrupt", "stop"}:
                runtime.interrupt_playback()
                receive.cancel()
                with suppress(asyncio.CancelledError):
                    await receive
                return None, event.kind
            event_task = asyncio.create_task(runtime.next_event())
    finally:
        if not event_task.done():
            event_task.cancel()


async def run_voice(agent: JarvisAgent, settings: Settings) -> int:
    runtime = InProcessVoiceRuntime(settings)
    lock = ProcessLock(settings.root)
    lock.acquire()
    clear_runtime_error()
    live: GeminiLiveSession | None = None
    try:
        await runtime.start()
        print("JARVIS v2", flush=True)
        print(f"Gemini 3.8 Live: {'available' if settings.gemini_api_key else 'not configured'}", flush=True)
        print(f"Extended Thinking: {'available' if settings.gemini_api_key else 'not configured'}", flush=True)
        print(f"Groq fallback: {'available' if settings.groq_api_key and settings.fallback_enabled else 'not configured'}", flush=True)
        print(f"Wake word: {runtime.wake_status}", flush=True)
        print("JARVIS online. Скажіть «Джарвіс» або натисніть Ctrl+Alt+J. Ctrl+C — вихід.", flush=True)
        conversation_active = False
        last_activity = time.monotonic()
        next_live_extended: bool | None = None
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
            if kind == "stop":
                print("JARVIS v2 зупинено через desktop-вікно.", flush=True)
                break
            if kind != "audio":
                continue
            conversation_active = True
            last_activity = time.monotonic()
            if settings.voice_mode in {"gemini", "fish"} and settings.gemini_api_key:
                try:
                    # AudioEvent.text is reserved for a future local recognizer.
                    # When present it gives the small deterministic fast path a
                    # true pre-cloud decision point; raw microphone audio remains
                    # Gemini Live's native transcription path.
                    if event.text:
                        fast = await agent.fast_path(event.text, voice=True)
                        if fast:
                            print(f"JARVIS: {fast.text}")
                            await runtime.speak_fallback(fast.voice_text)
                            continue
                    if live is None:
                        use_extended = next_live_extended if next_live_extended is not None else agent.needs_extended_thinking(event.text)
                        next_live_extended = None
                        live = GeminiLiveSession(settings, agent.tools, extended=use_extended)
                        await live.connect(agent.persona.prompt(PersonalityMode.CASUAL))
                    live.action_dispatched = False
                    await live.send_context(agent.live_guidance(event.text))
                    runtime.begin_response_timing()
                    await live.send_audio(event.audio)
                    delayed_audio: list[bytes] = []
                    state: dict[str, object] = {"stream_ready": None, "fast_reply": None, "user_recorded": False}

                    async def on_audio(
                        chunk: bytes, queue: list[bytes] = delayed_audio, status: dict[str, object] = state
                    ) -> None:
                        if status["stream_ready"] is True:
                            await runtime.enqueue_pcm(chunk)
                        else:
                            queue.append(chunk)

                    async def on_transcript(
                        transcript: str, queue: list[bytes] = delayed_audio, session: GeminiLiveSession = live,
                        status: dict[str, object] = state,
                    ) -> bool:
                        agent.record_user_turn(transcript)
                        status["user_recorded"] = True
                        status["fast_reply"] = await agent.fast_path(transcript, voice=True)
                        if status["fast_reply"]:
                            return True
                        # A current-info answer cannot be voiced as a fact until
                        # Live returns documented grounding metadata. Ordinary
                        # turns flush their queued initial chunks immediately.
                        status["stream_ready"] = agent.persona_mode(transcript) != PersonalityMode.LIVE_INFO
                        if status["stream_ready"]:
                            for queued in queue:
                                await runtime.enqueue_pcm(queued)
                            queue.clear()
                        await session.send_context(agent.live_guidance(transcript))
                        return False

                    receive = asyncio.create_task(
                        live.receive_turn(
                            on_audio=on_audio if settings.voice_mode == "gemini" else None,
                            execute_tool=agent.execute_live_tool,
                            on_input_transcript=on_transcript,
                        )
                    )
                    response, interrupted = await _receive_with_barge_in(runtime, receive)
                    if interrupted:
                        await live.close()
                        live = None
                        if interrupted == "stop":
                            break
                        continue
                    assert response is not None
                    fast_reply = state["fast_reply"]
                    if fast_reply:
                        print(f"JARVIS: {fast_reply.text}")
                        agent.record_assistant_turn(fast_reply.text)
                        await runtime.speak_fallback(fast_reply.voice_text)
                        await live.close()
                        live = None
                        continue
                    if response.user_text and not state["user_recorded"]:
                        agent.record_user_turn(response.user_text)
                    if response.text:
                        print(f"JARVIS: {response.text}")
                        agent.record_assistant_turn(response.text)
                    if response.sources and response.user_text:
                        agent.record_grounded_live(response.user_text, response.text, response.sources)
                        if settings.voice_mode == "gemini":
                            for queued in delayed_audio:
                                await runtime.enqueue_pcm(queued)
                    elif state["stream_ready"] is False:
                        delayed_audio.clear()
                        safe = "Не отримав перевірюваних джерел, тому не подаю це як актуальний факт."
                        print(f"JARVIS: {safe}")
                        await runtime.speak_fallback(safe)
                    if response.user_text:
                        desired_extended = agent.needs_extended_thinking(response.user_text)
                        current_extended = live.model == settings.gemini_extended_model
                        if desired_extended != current_extended:
                            next_live_extended = desired_extended
                            await live.close()
                            live = None
                    if settings.debug and runtime.first_audio_latency_ms is not None:
                        print(f"[Live] first-audio={runtime.first_audio_latency_ms:.0f}ms", flush=True)
                    if settings.voice_mode == "fish":
                        # Fish speaks the healthy Live transcript and never
                        # causes a second Gemini or Groq turn.
                        await runtime.speak_fallback(agent.persona.finish(response.text, voice=True))
                    continue
                except Exception as error:
                    message = _live_error_message(error)
                    write_runtime_error(message)
                    print(f"JARVIS: {message}", flush=True)
                    if settings.debug:
                        print(f"[Gemini] detail: {error}", flush=True)
                    action_dispatched = bool(live and live.action_dispatched)
                    if live:
                        await live.close()
                    live = None
                    if action_dispatched:
                        print("JARVIS: Зв'язок обірвався після запуску дії. Її стан невідомий; повторно не запускаю.")
                        continue
                    if not settings.groq_api_key or not settings.fallback_enabled:
                        await runtime.speak_fallback(message)
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
        try:
            if live:
                await live.close()
        finally:
            try:
                await runtime.stop()
            finally:
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
                    "wake_word": "optional local backend; hotkey reliable",
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
    except RuntimeError as error:
        # A second desktop window can race a runtime that is already alive.
        # Keep the normal GUI free of an alarming PyInstaller traceback.
        print(f"JARVIS v2: {error}", flush=True)


if __name__ == "__main__":
    main()
