from __future__ import annotations

import asyncio
import io
import os
import queue
import threading
import wave
from typing import Any


class InProcessVoiceRuntime:
    """Directly composes legacy-proven VAD/STT and Fish/Piper objects in one process."""

    def __init__(
        self,
        *,
        input_enabled: bool = True,
        tts_enabled: bool = True,
        custom_vocabulary: tuple[str, ...] = (),
    ) -> None:
        self.input_enabled = input_enabled
        self.tts_enabled = tts_enabled
        self.custom_vocabulary = custom_vocabulary
        self._input: Any = None
        self._voice: Any = None
        self._synthesis_request: Any = None
        self._started = False

    def _load(self) -> None:
        if self.input_enabled and self._input is None:
            from jarvis_v2.stt import SttPolicy

            os.environ["JARVIS_STT_PROMPT"] = SttPolicy(custom_vocabulary=self.custom_vocabulary).prompt()
            from voice_input_service.app import Settings as InputSettings
            from voice_input_service.app import VoiceInputEngine

            self._input = VoiceInputEngine(InputSettings.from_environment())
        if self.tts_enabled and self._voice is None:
            from voice_service.app import SynthesisRequest, voice

            self._voice = voice
            self._synthesis_request = SynthesisRequest

    async def start(self) -> None:
        self._load()
        if self.input_enabled:
            await asyncio.to_thread(self._input.start)
        if self.tts_enabled:
            threading.Thread(target=self._voice.piper.load, name="piper-lazy-load-v2", daemon=True).start()
            threading.Thread(target=self._voice.prewarm, name="fish-prewarm-v2", daemon=True).start()
        self._started = True

    async def stop(self) -> None:
        if self._started and self.input_enabled:
            await asyncio.to_thread(self._input.stop)
        self._started = False

    async def next_event(self, timeout: float = 0.5) -> dict[str, object]:
        if not self.input_enabled:
            await asyncio.sleep(timeout)
            return {"type": "timeout"}
        try:
            return await asyncio.to_thread(self._input.events.get, True, timeout)
        except queue.Empty:
            return {"type": "timeout"}

    def set_state(self, conversation_active: bool, speaking: bool) -> None:
        if self.input_enabled:
            self._input.set_state(conversation_active, speaking)

    async def activation_cue(self) -> None:
        if not self.input_enabled:
            return

        def beep() -> None:
            import winsound

            winsound.Beep(880, 80)

        await asyncio.to_thread(beep)

    async def speak(self, text: str) -> None:
        if not self.tts_enabled or not text.strip():
            return
        self.set_state(True, True)
        try:
            result = await asyncio.to_thread(self._voice.synthesize, self._synthesis_request(text=text))
            await asyncio.to_thread(self._play_wav, result.audio)
        finally:
            self.set_state(True, False)

    @staticmethod
    def _play_wav(audio: bytes) -> None:
        import numpy as np
        import sounddevice as sd

        with wave.open(io.BytesIO(audio), "rb") as stream:
            channels = stream.getnchannels()
            sample_rate = stream.getframerate()
            width = stream.getsampwidth()
            frames = stream.readframes(stream.getnframes())
        if width != 2:
            raise ValueError("JARVIS v2 playback expects signed 16-bit PCM WAV")
        samples = np.frombuffer(frames, dtype="<i2")
        if channels > 1:
            samples = samples.reshape(-1, channels)
        sd.play(samples, sample_rate, blocking=True)
