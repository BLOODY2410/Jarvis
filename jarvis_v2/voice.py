"""Local activation, VAD, playback, and optional Groq/Fish fallback.

No HTTP server is started here. Audio stays local until a wake/hotkey activation;
the normal cloud destination is Gemini Live. Groq transcription is used only if the
configured emergency fallback has to take over.
"""

from __future__ import annotations

import asyncio
import io
import queue
import threading
import wave
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from jarvis_v2.config import Settings


@dataclass(slots=True)
class AudioEvent:
    kind: str
    audio: bytes = b""
    text: str = ""
    epoch: int = 0


class InProcessVoiceRuntime:
    """One-process microphone runtime with local VAD and Ctrl+Alt+J activation.

    An installed OpenWakeWord backend is used opportunistically. The hotkey remains
    available when a wake model is absent, which avoids a silent, permanently-open
    microphone fallback.
    """

    sample_rate = 16_000
    frame_ms = 20

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._frames: queue.Queue[bytes] = queue.Queue(maxsize=250)
        self._events: queue.Queue[AudioEvent] = queue.Queue()
        self._stream: Any = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._active = threading.Event()
        self._speaking = threading.Event()
        self._playback_stop = threading.Event()
        self._wake_model: Any = None
        self._activation_epoch = 0
        self._activation_lock = threading.Lock()

    async def start(self) -> None:
        self._load_wake_model()
        self._start_hotkey()
        if not self.settings.voice_input_enabled:
            return
        try:
            import sounddevice as sd
        except ImportError as error:
            raise RuntimeError("sounddevice is required for voice input.") from error

        frame_samples = self.sample_rate * self.frame_ms // 1000

        def callback(indata: Any, _frames: int, _time: object, status: object) -> None:
            if status and self.settings.debug:
                print(f"[Voice] input status={status}")
            with suppress(queue.Full):
                self._frames.put_nowait(bytes(indata))

        self._stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=frame_samples,
            channels=1,
            dtype="int16",
            callback=callback,
        )
        self._stream.start()
        self._worker = threading.Thread(target=self._capture_loop, name="jarvis-vad", daemon=True)
        self._worker.start()

    def _load_wake_model(self) -> None:
        try:
            from openwakeword.model import Model

            self._wake_model = Model()
        except Exception:
            self._wake_model = None

    def _start_hotkey(self) -> None:
        try:
            import keyboard

            keyboard.add_hotkey("ctrl+alt+j", self.activate)
        except Exception:
            # The documented local wake backend remains available when keyboard
            # hooks require elevation or are unavailable on a target PC.
            pass

    def activate(self) -> None:
        if not self._active.is_set():
            with self._activation_lock:
                self._activation_epoch += 1
                epoch = self._activation_epoch
                self._active.set()
            self._events.put(AudioEvent("wake", epoch=epoch))

    def deactivate(self) -> None:
        """Close the upload window and discard captured audio from that window."""
        self._active.clear()
        with self._activation_lock:
            self._activation_epoch += 1
        kept: list[AudioEvent] = []
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            if event.kind != "audio":
                kept.append(event)
        for event in kept:
            self._events.put(event)

    def _wake_detected(self, frame: bytes) -> bool:
        if self._wake_model is None or self._speaking.is_set():
            return False
        try:
            import numpy as np

            scores = self._wake_model.predict(np.frombuffer(frame, dtype="<i2"))
            return any(float(score) >= 0.5 for score in scores.values())
        except Exception:
            return False

    def _capture_loop(self) -> None:
        try:
            import webrtcvad
        except ImportError:
            return
        vad = webrtcvad.Vad(2)
        pre_roll: deque[bytes] = deque(maxlen=24)
        utterance: list[bytes] = []
        silence_frames = 0
        speaking = False
        interrupt_frames = 0
        while not self._stop.is_set():
            try:
                frame = self._frames.get(timeout=0.2)
            except queue.Empty:
                continue
            pre_roll.append(frame)
            if self._wake_detected(frame):
                self.activate()
            if not self._active.is_set():
                continue
            voiced = vad.is_speech(frame, self.sample_rate)
            if self._speaking.is_set():
                interrupt_frames = interrupt_frames + 1 if voiced else 0
                if interrupt_frames >= 3:
                    self.interrupt_playback()
                    self._events.put(AudioEvent("interrupt"))
                    interrupt_frames = 0
                continue
            if voiced and not speaking:
                utterance = list(pre_roll)
                speaking = True
                silence_frames = 0
                with self._activation_lock:
                    utterance_epoch = self._activation_epoch
            if not speaking:
                continue
            utterance.append(frame)
            silence_frames = 0 if voiced else silence_frames + 1
            if silence_frames >= 24 or len(utterance) >= 1_000:
                audio = b"".join(utterance)
                speaking = False
                utterance = []
                silence_frames = 0
                if len(audio) >= self.sample_rate * 2 // 10 and self._active.is_set():
                    with self._activation_lock:
                        current_epoch = self._activation_epoch
                    if utterance_epoch == current_epoch:
                        self._events.put(AudioEvent("audio", audio=audio, epoch=utterance_epoch))

    async def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            await asyncio.to_thread(self._stream.stop)
            await asyncio.to_thread(self._stream.close)
        self._stream = None

    async def next_event(self, timeout: float = 0.5) -> AudioEvent:
        try:
            return await asyncio.to_thread(self._events.get, True, timeout)
        except queue.Empty:
            return AudioEvent("timeout")

    async def activation_cue(self) -> None:
        try:
            import winsound

            await asyncio.to_thread(winsound.Beep, 880, 80)
        except Exception:
            pass

    def interrupt_playback(self) -> None:
        self._playback_stop.set()

    async def play_pcm(self, chunks: list[bytes], sample_rate: int = 24_000) -> None:
        if not chunks:
            return
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            return
        self._speaking.set()
        self._playback_stop.clear()
        try:
            for chunk in chunks:
                if self._playback_stop.is_set():
                    break
                samples = np.frombuffer(chunk, dtype="<i2")
                await asyncio.to_thread(sd.play, samples, sample_rate, blocking=True)
        finally:
            self._speaking.clear()

    async def speak_fallback(self, text: str) -> None:
        """Fish SDK when configured, with Windows SAPI as a local last resort."""
        if not text.strip() or not self.settings.tts_enabled:
            return
        if self.settings.fish_api_key and self.settings.fish_voice_id:
            try:
                audio = await asyncio.to_thread(self._fish_audio, text)
                await self._play_encoded(audio)
                return
            except Exception as error:
                if self.settings.debug:
                    print(f"[Fish] fallback failed: {type(error).__name__}")
        await asyncio.to_thread(self._sapi, text)

    def _fish_audio(self, text: str) -> bytes:
        from fishaudio import FishAudio

        client = FishAudio(api_key=self.settings.fish_api_key)
        return b"".join(
            client.tts.stream_websocket(
                iter((text,)), reference_id=self.settings.fish_voice_id, latency="balanced"
            )
        )

    async def _play_encoded(self, audio: bytes) -> None:
        import soundfile as sf

        samples, sample_rate = await asyncio.to_thread(sf.read, io.BytesIO(audio), dtype="int16")
        try:
            import sounddevice as sd

            self._speaking.set()
            await asyncio.to_thread(sd.play, samples, sample_rate, blocking=True)
        finally:
            self._speaking.clear()

    @staticmethod
    def _sapi(text: str) -> None:
        try:
            import win32com.client

            win32com.client.Dispatch("SAPI.SpVoice").Speak(text)
        except Exception:
            pass

    async def transcribe_fallback(self, pcm: bytes) -> str:
        if not self.settings.fallback_enabled or not self.settings.groq_api_key:
            return ""
        try:
            from groq import Groq
        except ImportError:
            return ""

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            output.writeframes(pcm)
        buffer.name = "utterance.wav"
        prompt = "Українська, російська або суржикова команда для Джарвіса. " + ", ".join(
            self.settings.custom_vocabulary
        )
        try:
            result = await asyncio.to_thread(
                Groq(api_key=self.settings.groq_api_key).audio.transcriptions.create,
                file=buffer,
                model="whisper-large-v3-turbo",
                language=None,
                prompt=prompt,
                response_format="json",
            )
        except Exception:
            return ""
        return str(getattr(result, "text", "")).strip()
