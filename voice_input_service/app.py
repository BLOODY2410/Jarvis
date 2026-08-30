from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import io
import json
import logging
import os
import queue
import re
import shutil
import threading
import time
import wave
import zipfile
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
import webrtcvad
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

SERVICE_ROOT = Path(__file__).resolve().parent
load_dotenv(SERVICE_ROOT.parent / ".env")
logging.basicConfig(level=os.getenv("JARVIS_INPUT_LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("jarvis.voice_input")

SAMPLE_RATE = 16_000
FRAME_MS = 80
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1_000
VOSK_MODEL_NAME = "vosk-model-small-uk-v3-nano"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() not in {"0", "false", "no", "off"}


def dbfs(value: float) -> float:
    return 20.0 * float(np.log10(max(value, 1e-9)))


def audio_levels(frame: bytes) -> tuple[float, float]:
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
    if not samples.size:
        return 0.0, 0.0
    return float(np.sqrt(np.mean(samples * samples))), float(np.max(np.abs(samples)))


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    whisper_model: str
    language: str
    device: int | str | None
    wake_threshold: float
    vad_aggressiveness: int
    silence_ms: int
    max_utterance_secs: float
    barge_in_chunks: int
    barge_in_enabled: bool
    min_speech_ms: int
    min_audio_rms: float
    post_tts_guard_ms: int
    stt_prompt: str
    diagnostic: bool = False
    level_log_interval_ms: int = 1_000
    pre_roll_ms: int = 560
    post_roll_ms: int = 480
    diagnostic_dir: Path = SERVICE_ROOT / "diagnostics"
    stt_response_format: str = "verbose_json"
    wake_variants: tuple[str, ...] = ("джарвіс", "джарвис", "джарвиз")
    wake_vosk_max_edit_distance: int = 1
    vad_energy_ratio: float = 1.10
    vad_energy_delta: float = 0.004
    vad_start_chunks: int = 1
    activation_mode: str = "hotkey"
    hotkey_enabled: bool = True
    fast_whisper_model: str = "whisper-large-v3-turbo"
    fast_silence_ms: int = 560
    long_utterance_threshold_ms: int = 3_000
    fast_stt_max_speech_ms: int = 3_500
    vad_resume_chunks: int = 2

    @classmethod
    def from_environment(cls) -> "Settings":
        device_text = os.getenv("JARVIS_MIC_DEVICE", "").strip()
        device: int | str | None = None
        if device_text:
            device = int(device_text) if device_text.isdigit() else device_text
        return cls(
            groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
            whisper_model=os.getenv("JARVIS_WHISPER_MODEL", "whisper-large-v3"),
            language=os.getenv("JARVIS_STT_LANGUAGE", "uk"),
            device=device,
            wake_threshold=env_float("JARVIS_WAKE_THRESHOLD", 0.45),
            vad_aggressiveness=int(env_float("JARVIS_VAD_AGGRESSIVENESS", 1)),
            silence_ms=int(env_float("JARVIS_END_SILENCE_MS", 1_200)),
            max_utterance_secs=env_float("JARVIS_MAX_UTTERANCE_SECS", 20.0),
            barge_in_chunks=max(1, int(env_float("JARVIS_BARGE_IN_CHUNKS", 3))),
            barge_in_enabled=env_bool("JARVIS_BARGE_IN_ENABLED", False),
            min_speech_ms=max(0, int(env_float("JARVIS_MIN_SPEECH_MS", 300))),
            min_audio_rms=max(0.0, env_float("JARVIS_MIN_AUDIO_RMS", 0.0015)),
            post_tts_guard_ms=max(0, int(env_float("JARVIS_POST_TTS_GUARD_MS", 800))),
            stt_prompt=os.getenv(
                "JARVIS_STT_PROMPT",
                "Українська голосова команда для персонального асистента Джарвіс. "
                "Можливі назви Windows, YouTube, Google, браузер, PowerShell, Steam та Discord.",
            ),
            diagnostic=env_bool("JARVIS_DIAGNOSTIC", True),
            level_log_interval_ms=max(100, int(env_float("JARVIS_LEVEL_LOG_INTERVAL_MS", 1_000))),
            pre_roll_ms=max(FRAME_MS, int(env_float("JARVIS_PRE_ROLL_MS", 560))),
            post_roll_ms=max(0, int(env_float("JARVIS_POST_ROLL_MS", 480))),
            diagnostic_dir=Path(os.getenv("JARVIS_DIAGNOSTIC_DIR", str(SERVICE_ROOT / "diagnostics"))).resolve(),
            stt_response_format=os.getenv("JARVIS_STT_RESPONSE_FORMAT", "verbose_json").strip() or "verbose_json",
            wake_variants=tuple(
                item.strip()
                for item in os.getenv("JARVIS_WAKE_VARIANTS", "джарвіс,джарвис,джарвиз").split(",")
                if item.strip()
            ),
            wake_vosk_max_edit_distance=max(
                0,
                int(env_float("JARVIS_WAKE_VOSK_MAX_EDIT_DISTANCE", 1)),
            ),
            vad_energy_ratio=max(1.0, env_float("JARVIS_VAD_ENERGY_RATIO", 1.10)),
            vad_energy_delta=max(0.0, env_float("JARVIS_VAD_ENERGY_DELTA", 0.004)),
            vad_start_chunks=max(1, int(env_float("JARVIS_VAD_START_CHUNKS", 2))),
            activation_mode=os.getenv("JARVIS_ACTIVATION_MODE", "hotkey").strip().lower(),
            hotkey_enabled=env_bool("JARVIS_HOTKEY_ENABLED", True),
            fast_whisper_model=os.getenv(
                "JARVIS_FAST_WHISPER_MODEL", "whisper-large-v3-turbo"
            ).strip() or "whisper-large-v3-turbo",
            fast_silence_ms=max(
                400, min(600, int(env_float("JARVIS_FAST_END_SILENCE_MS", 560)))
            ),
            long_utterance_threshold_ms=max(
                1_000, int(env_float("JARVIS_LONG_UTTERANCE_THRESHOLD_MS", 3_000))
            ),
            fast_stt_max_speech_ms=max(
                500, int(env_float("JARVIS_FAST_STT_MAX_SPEECH_MS", 3_500))
            ),
            vad_resume_chunks=max(1, int(env_float("JARVIS_VAD_RESUME_CHUNKS", 2))),
        )


class StateRequest(BaseModel):
    conversation_active: bool
    speaking: bool


class WakeWavRequest(BaseModel):
    path: str
    threshold: float | None = None


class VoiceInputEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.events: queue.Queue[dict[str, object]] = queue.Queue(maxsize=32)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._conversation_active = False
        self._speaking = False
        self._wake_model = None
        self._wake_model_name = "hey_jarvis"
        self._wake_lock = threading.Lock()
        self._vosk_recognizer = None
        self._vad = webrtcvad.Vad(max(0, min(3, settings.vad_aggressiveness)))
        self._capture: list[bytes] = []
        self._speech_seen = False
        self._silence_chunks = 0
        self._speech_chunks = 0
        self._consecutive_speech_chunks = 0
        self._interrupt_sent = False
        self._stt_busy = False
        self._pre_roll: deque[bytes] = deque(maxlen=frames_for_ms(settings.pre_roll_ms, minimum=1))
        self._ignore_until = 0.0
        self._capture_started_at: float | None = None
        self._last_level_log = 0.0
        self._last_rms = 0.0
        self._last_peak = 0.0
        self._last_vad_state = False
        self._last_vad_raw = False
        self._last_energy_gate = False
        self._last_wake_score = 0.0
        self._selected_device: dict[str, object] | None = None
        self._last_transcript: dict[str, object] | None = None
        self._utterance_number = 0
        self._level_history: deque[tuple[float, float]] = deque(maxlen=max(10, 10_000 // FRAME_MS))
        self._noise_history: deque[float] = deque(maxlen=max(25, 10_000 // FRAME_MS))
        self._vad_start_run = 0
        self._vad_resume_run = 0
        self._device: int | str | None = settings.device
        self._hotkey_thread: threading.Thread | None = None
        self._last_mic_end_unix_ms: int | None = None

    def start(self) -> None:
        if not self.settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is required for Groq Whisper STT")
        if self.settings.activation_mode == "wake":
            self._load_wake_model()
            self._load_ukrainian_wake_model()
        elif self.settings.activation_mode not in {"hotkey", "api"}:
            raise RuntimeError("JARVIS_ACTIVATION_MODE must be hotkey, api, or wake")
        self._log_audio_devices()
        self.settings.diagnostic_dir.mkdir(parents=True, exist_ok=True)
        LOGGER.info(
            "Audio config: sample_rate=%d Hz channels=1 chunk=%d samples/%d ms VAD=%d pre_roll=%d ms post_roll=%d ms end_silence=%d ms",
            SAMPLE_RATE,
            FRAME_SAMPLES,
            FRAME_MS,
            self.settings.vad_aggressiveness,
            self.settings.pre_roll_ms,
            self.settings.post_roll_ms,
            self.settings.silence_ms,
        )
        LOGGER.info(
            "Mode: diagnostic=%s barge_in=%s level_log_interval=%d ms; diagnostic files: %s",
            self.settings.diagnostic,
            self.settings.barge_in_enabled,
            self.settings.level_log_interval_ms,
            self.settings.diagnostic_dir,
        )
        LOGGER.info(
            "Adaptive VAD gate: energy_ratio=%.2f energy_delta=%.4f start_chunks=%d",
            self.settings.vad_energy_ratio,
            self.settings.vad_energy_delta,
            self.settings.vad_start_chunks,
        )
        LOGGER.info(
            "Wake config: openWakeWord_threshold=%.3f Vosk_variants=%s max_edit_distance=%d",
            self.settings.wake_threshold,
            ",".join(self.settings.wake_variants),
            self.settings.wake_vosk_max_edit_distance,
        )
        self._thread = threading.Thread(target=self._audio_loop, name="jarvis-microphone", daemon=True)
        self._thread.start()
        if self.settings.activation_mode == "hotkey" and self.settings.hotkey_enabled:
            self._hotkey_thread = threading.Thread(
                target=self._hotkey_loop, name="jarvis-hotkey", daemon=True
            )
            self._hotkey_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def set_state(self, conversation_active: bool, speaking: bool) -> None:
        with self._lock:
            was_speaking = self._speaking
            self._conversation_active = conversation_active
            self._speaking = speaking
            if was_speaking and not speaking:
                self._ignore_until = time.monotonic() + self.settings.post_tts_guard_ms / 1_000
                self._reset_capture()
                self._pre_roll.clear()
            if not conversation_active:
                self._reset_capture()
                self._pre_roll.clear()
                self._clear_events()
                if self._vosk_recognizer is not None:
                    self._vosk_recognizer.Reset()

    def get_state(self) -> dict[str, object]:
        recent_levels = list(self._level_history)
        recent_rms = np.array([item[0] for item in recent_levels], dtype=np.float32)
        recent_peak = np.array([item[1] for item in recent_levels], dtype=np.float32)
        level_window = {
            "seconds": round(len(recent_levels) * FRAME_MS / 1_000, 1),
            "rms_min": round(float(np.min(recent_rms)), 6) if recent_rms.size else 0.0,
            "rms_median": round(float(np.median(recent_rms)), 6) if recent_rms.size else 0.0,
            "rms_p95": round(float(np.percentile(recent_rms, 95)), 6) if recent_rms.size else 0.0,
            "peak_max": round(float(np.max(recent_peak)), 6) if recent_peak.size else 0.0,
        }
        with self._lock:
            return {
                "conversation_active": self._conversation_active,
                "speaking": self._speaking,
                "stt_busy": self._stt_busy,
                "running": not self._stop.is_set(),
                "diagnostic": self.settings.diagnostic,
                "device": self._selected_device,
                "rms": round(self._last_rms, 6),
                "peak": round(self._last_peak, 6),
                "rms_dbfs": round(dbfs(self._last_rms), 1),
                "peak_dbfs": round(dbfs(self._last_peak), 1),
                "vad_speech": self._last_vad_state,
                "vad_raw": self._last_vad_raw,
                "energy_gate": self._last_energy_gate,
                "noise_floor_rms": round(self._noise_floor(), 6),
                "energy_gate_rms": round(self._energy_threshold(), 6),
                "last_transcript": self._last_transcript,
                "recent_level_window": level_window,
            }

    @staticmethod
    def available_input_devices() -> list[dict[str, object]]:
        devices: list[dict[str, object]] = []
        default_input = None
        try:
            default_input = int(sd.default.device[0])
        except (TypeError, ValueError, IndexError):
            pass
        for index, device in enumerate(sd.query_devices()):
            if int(device.get("max_input_channels", 0)) <= 0:
                continue
            devices.append(
                {
                    "index": index,
                    "name": str(device.get("name", "")),
                    "hostapi": int(device.get("hostapi", -1)),
                    "max_input_channels": int(device.get("max_input_channels", 0)),
                    "default_samplerate": float(device.get("default_samplerate", 0.0)),
                    "is_default": index == default_input,
                }
            )
        return devices

    def _log_audio_devices(self) -> None:
        devices = self.available_input_devices()
        LOGGER.info("Available input devices (%d):", len(devices))
        for device in devices:
            LOGGER.info(
                "  [%s]%s %s | inputs=%s | default_rate=%.0f Hz | hostapi=%s",
                device["index"],
                " DEFAULT" if device["is_default"] else "",
                device["name"],
                device["max_input_channels"],
                device["default_samplerate"],
                device["hostapi"],
            )
        try:
            self._device = self._select_device(devices)
            selected = sd.query_devices(self._device, "input")
            selected_index = int(self._device) if isinstance(self._device, int) else self._resolve_selected_device_index(selected)
            self._selected_device = {
                "requested": self.settings.device if self.settings.device is not None else "auto",
                "index": selected_index,
                "name": str(selected["name"]),
                "max_input_channels": int(selected["max_input_channels"]),
                "default_samplerate": float(selected["default_samplerate"]),
            }
            sd.check_input_settings(device=self._device, channels=1, dtype="int16", samplerate=SAMPLE_RATE)
            LOGGER.info(
                "Selected microphone: requested=%r resolved=[%s] %s; 16 kHz mono int16 supported",
                self.settings.device if self.settings.device is not None else "auto",
                selected_index,
                selected["name"],
            )
        except Exception:
            LOGGER.exception("Selected microphone cannot open with the required 16 kHz mono format")
            raise

    def _select_device(self, devices: list[dict[str, object]]) -> int | str:
        """Resolve an explicit device or choose a real microphone, never loopback."""
        if self.settings.device is not None:
            if isinstance(self.settings.device, int):
                if not any(item["index"] == self.settings.device for item in devices):
                    raise RuntimeError(f"Configured microphone index {self.settings.device} does not exist")
                return self.settings.device
            needle = str(self.settings.device).casefold()
            matches = [item for item in devices if needle in str(item["name"]).casefold()]
            if len(matches) != 1:
                names = ", ".join(f"[{item['index']}] {item['name']}" for item in matches) or "none"
                raise RuntimeError(f"JARVIS_MIC_DEVICE must match exactly one input; matches: {names}")
            return int(matches[0]["index"])

        blocked = ("stereo mix", "loopback", "what u hear", "output", "мікшер", "стерео")
        safe = [
            item for item in devices
            if not any(word in str(item["name"]).casefold() for word in blocked)
        ]
        default = next((item for item in safe if item["is_default"]), None)
        if default is not None:
            return int(default["index"])
        preferred = next(
            (
                item for item in safe
                if any(word in str(item["name"]).casefold() for word in ("microphone", "mic", "мікрофон"))
            ),
            None,
        )
        if preferred is not None:
            return int(preferred["index"])
        if safe:
            return int(safe[0]["index"])
        raise RuntimeError("No safe microphone input found; set JARVIS_MIC_DEVICE explicitly")

    @staticmethod
    def _resolve_selected_device_index(selected: object) -> int | None:
        if "index" in selected:
            return int(selected["index"])
        selected_name = str(selected["name"])
        for index, device in enumerate(sd.query_devices()):
            if device["name"] == selected_name and int(device["max_input_channels"]) > 0:
                return index
        return None

    def stop_listening(self) -> None:
        self._stop.set()
        self._emit({"type": "stopped"})

    def force_activate(self) -> None:
        with self._lock:
            if self._speaking or self._stt_busy:
                LOGGER.info("Activation ignored while TTS/STT is busy (half-duplex)")
                return
            self._conversation_active = True
            self._speaking = False
            self._ignore_until = 0.0
            self._reset_capture()
            self._pre_roll.clear()
        if self._vosk_recognizer is not None:
            self._vosk_recognizer.Reset()
        LOGGER.info("Forced activation: wake detector bypassed; microphone -> VAD -> Whisper test is active")
        self._emit({"type": "wake"})

    def _hotkey_loop(self) -> None:
        """Windows global Ctrl+Alt+J fallback without a keyboard-hook package."""
        if os.name != "nt":
            LOGGER.warning("Global hotkey is only available on Windows; use POST /activate")
            return
        user32 = ctypes.windll.user32
        hotkey_id = 0x4A17
        modifiers = 0x0001 | 0x0002 | 0x4000  # ALT | CTRL | NOREPEAT
        if not user32.RegisterHotKey(None, hotkey_id, modifiers, ord("J")):
            LOGGER.warning("Could not register Ctrl+Alt+J; POST /activate remains available")
            return
        LOGGER.info("Activation ready: press Ctrl+Alt+J or call POST /activate")
        message = ctypes.wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312 and message.wParam == hotkey_id:
                    self.force_activate()
        finally:
            user32.UnregisterHotKey(None, hotkey_id)

    def _load_wake_model(self) -> None:
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        model_dir = SERVICE_ROOT / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / "hey_jarvis_v0.1.onnx"
        feature_path = model_dir / "embedding_model.onnx"
        melspec_path = model_dir / "melspectrogram.onnx"
        if not all(path.exists() for path in (model_path, feature_path, melspec_path)):
            LOGGER.info("Downloading official openWakeWord 'hey jarvis' ONNX models...")
            download_models(["hey_jarvis"], str(model_dir))
        self._wake_model = Model(
            wakeword_models=[str(model_path)],
            inference_framework="onnx",
            embedding_model_path=str(feature_path),
            melspec_model_path=str(melspec_path),
        )
        self._wake_model_name = next(iter(self._wake_model.models))
        LOGGER.info("Wake detector loaded: %s", self._wake_model_name)

    def _load_ukrainian_wake_model(self) -> None:
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        model_dir = SERVICE_ROOT / "models" / VOSK_MODEL_NAME
        if not model_dir.exists():
            LOGGER.info("Downloading lightweight Ukrainian wake model (~73 MB)...")
            archive_path = SERVICE_ROOT / "models" / f"{VOSK_MODEL_NAME}.zip"
            with requests.get(VOSK_MODEL_URL, stream=True, timeout=120) as response:
                response.raise_for_status()
                with archive_path.open("wb") as target:
                    for chunk in response.iter_content(1024 * 1024):
                        target.write(chunk)
            with zipfile.ZipFile(archive_path) as archive:
                root = (SERVICE_ROOT / "models").resolve()
                for member in archive.infolist():
                    destination = (root / member.filename).resolve()
                    if root not in destination.parents and destination != root:
                        raise RuntimeError("Unsafe path in Vosk model archive")
                archive.extractall(root)
            archive_path.unlink(missing_ok=True)
        # A strict grammar makes this small model return an empty result when a
        # speaker's pronunciation is just outside its lexicon. Full decoding is
        # still lightweight here; matching below remains restricted to a close
        # phonetic form of the single wake word.
        self._vosk_recognizer = KaldiRecognizer(Model(str(model_dir)), SAMPLE_RATE)
        LOGGER.info("Ukrainian wake detector loaded: %s", VOSK_MODEL_NAME)

    def _audio_loop(self) -> None:
        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=FRAME_SAMPLES,
                device=self._device,
                dtype="int16",
                channels=1,
            ) as stream:
                LOGGER.info(
                    "Microphone stream opened: sample_rate=%s Hz channels=%s chunk=%s samples (%d ms); activation=%s",
                    getattr(stream, "samplerate", SAMPLE_RATE),
                    getattr(stream, "channels", 1),
                    getattr(stream, "blocksize", FRAME_SAMPLES),
                    FRAME_MS,
                    self.settings.activation_mode,
                )
                while not self._stop.is_set():
                    frame, overflowed = stream.read(FRAME_SAMPLES)
                    if overflowed:
                        LOGGER.warning("Microphone input overflow")
                    self.process_frame(bytes(frame))
        except Exception as error:
            LOGGER.exception("Microphone loop stopped")
            self._emit({"type": "error", "message": f"Помилка мікрофона: {error}"})
            self._emit({"type": "stopped"})

    def process_frame(self, frame: bytes) -> None:
        """Process one 80 ms PCM frame. Public for deterministic unit tests."""
        rms, peak = audio_levels(frame)
        self._last_rms = rms
        self._last_peak = peak
        self._level_history.append((rms, peak))
        with self._lock:
            active = self._conversation_active
            speaking = self._speaking
            stt_busy = self._stt_busy

        if not active:
            self._noise_history.append(rms)
            self._pre_roll.append(frame)
            if self.settings.activation_mode != "wake":
                self._log_levels_if_due(rms, peak, False)
                return
            samples = np.frombuffer(frame, dtype=np.int16)
            with self._wake_lock:
                predictions = self._wake_model.predict(samples)
                score = float(predictions.get(self._wake_model_name, 0.0))
                ukrainian_text = self._ukrainian_wake_text(frame)
            self._last_wake_score = score
            self._log_levels_if_due(rms, peak, False, score, ukrainian_text)
            if score >= self.settings.wake_threshold or contains_wake_word(
                ukrainian_text,
                self.settings.wake_variants,
                self.settings.wake_vosk_max_edit_distance,
            ):
                source = f"openWakeWord score {score:.3f}" if score >= self.settings.wake_threshold else f"Vosk: {ukrainian_text}"
                LOGGER.info("Wake word detected (%s)", source)
                with self._lock:
                    self._conversation_active = True
                with self._wake_lock:
                    if self._vosk_recognizer is not None:
                        self._vosk_recognizer.Reset()
                # The wake phrase is not a command. Discard it and wait for the
                # user's next utterance instead of sending "Джарвіс" to Whisper.
                self._pre_roll.clear()
                self._reset_capture()
                self._emit({"type": "wake"})
            return

        if stt_busy:
            self._log_levels_if_due(rms, peak, None)
            # Keep this path explicitly half-duplex. Buffering only part of a
            # second utterance while Whisper is busy creates a convincing but
            # truncated pseudo-capture when STT completes.
            self._reset_capture()
            self._pre_roll.clear()
            return

        if time.monotonic() < self._ignore_until:
            self._log_levels_if_due(rms, peak, None)
            self._reset_capture()
            self._pre_roll.clear()
            return

        # Without acoustic echo cancellation, loudspeakers are indistinguishable
        # from the user. Default to half-duplex and enable barge-in explicitly for
        # a headset/directional microphone.
        if speaking and not self.settings.barge_in_enabled:
            self._log_levels_if_due(rms, peak, None)
            self._reset_capture()
            self._pre_roll.clear()
            return

        raw_speech = self._is_speech(frame)
        energy_threshold = self._energy_threshold()
        energy_pass = rms >= energy_threshold
        self._last_vad_raw = raw_speech
        self._last_energy_gate = energy_pass
        # Energy is the deterministic primary detector. WebRTC remains useful
        # telemetry, but cannot discard Ukrainian speech on its own.
        candidate_speech = energy_pass
        if not self._speech_seen and not energy_pass:
            # Continue adapting during an active conversation, but only from
            # frames still below the current energy threshold. This also works
            # when WebRTC mistakes steady Realtek noise for speech. The rolling
            # median makes the update resistant to isolated missed speech.
            self._noise_history.append(rms)
        if not self._speech_seen:
            self._vad_start_run = self._vad_start_run + 1 if candidate_speech else 0
            speech = candidate_speech and self._vad_start_run >= self.settings.vad_start_chunks
        else:
            if candidate_speech:
                self._vad_resume_run += 1
                speech = (
                    self._silence_chunks == 0
                    or self._vad_resume_run >= self.settings.vad_resume_chunks
                )
            else:
                self._vad_resume_run = 0
                speech = False
        previous_vad_state = self._last_vad_state
        self._last_vad_state = speech
        self._log_levels_if_due(rms, peak, speech)
        if self.settings.diagnostic and speech != previous_vad_state:
            LOGGER.info("VAD transition: %s", "SPEECH" if speech else "SILENCE")
        if speech:
            self._last_mic_end_unix_ms = int(time.time() * 1_000)
            self._speech_chunks += 1
            self._consecutive_speech_chunks += 1
            self._silence_chunks = 0
            if not self._speech_seen:
                self._speech_chunks = self._vad_start_run
                self._consecutive_speech_chunks = self._vad_start_run
                self._capture = list(self._pre_roll)
                self._pre_roll.clear()
                self._capture_started_at = time.monotonic()
                LOGGER.info(
                    "VAD recording START: pre_roll=%d ms rms=%.4f (%.1f dBFS) peak=%.4f (%.1f dBFS) noise=%.4f gate=%.4f",
                    len(self._capture) * FRAME_MS,
                    rms,
                    dbfs(rms),
                    peak,
                    dbfs(peak),
                    self._noise_floor(),
                    energy_threshold,
                )
            self._speech_seen = True
            if speaking and not self._interrupt_sent and self._consecutive_speech_chunks >= self.settings.barge_in_chunks:
                self._interrupt_sent = True
                LOGGER.info("Barge-in detected")
                self._emit({"type": "interrupt"})
        elif self._speech_seen:
            self._silence_chunks += 1
            self._consecutive_speech_chunks = 0
        else:
            self._consecutive_speech_chunks = 0
            self._pre_roll.append(frame)

        if self._speech_seen:
            self._capture.append(frame)

        voiced_ms = self._speech_chunks * FRAME_MS
        adaptive_silence_ms = (
            min(self.settings.fast_silence_ms, self.settings.silence_ms)
            if voiced_ms < self.settings.long_utterance_threshold_ms
            else self.settings.silence_ms
        )
        silence_limit = frames_for_ms(adaptive_silence_ms, minimum=1)
        max_frames = max(1, int(self.settings.max_utterance_secs * 1_000 // FRAME_MS))
        if self._speech_seen and (self._silence_chunks >= silence_limit or len(self._capture) >= max_frames):
            raw_frames = list(self._capture)
            end_reason = "end_silence" if self._silence_chunks >= silence_limit else "max_duration"
            post_roll_frames = min(self._silence_chunks, frames_for_ms(self.settings.post_roll_ms))
            trailing_to_remove = max(0, self._silence_chunks - post_roll_frames)
            whisper_frames = raw_frames[:-trailing_to_remove] if trailing_to_remove else raw_frames
            speech_ms = self._speech_chunks * FRAME_MS
            raw_ms = len(raw_frames) * FRAME_MS
            whisper_ms = len(whisper_frames) * FRAME_MS
            LOGGER.info(
                "VAD recording END: reason=%s raw=%d ms whisper=%d ms voiced=%d ms trailing_silence=%d ms post_roll=%d ms adaptive_limit=%d ms",
                end_reason,
                raw_ms,
                whisper_ms,
                speech_ms,
                self._silence_chunks * FRAME_MS,
                post_roll_frames * FRAME_MS,
                adaptive_silence_ms,
            )
            self._reset_capture()
            with self._lock:
                self._stt_busy = True
            LOGGER.info("Capture paused until the current Whisper request completes")
            threading.Thread(
                target=self._transcribe,
                args=(raw_frames, whisper_frames, speech_ms),
                daemon=True,
                name="jarvis-whisper",
            ).start()

    def _log_levels_if_due(
        self,
        rms: float,
        peak: float,
        vad_speech: bool | None,
        wake_score: float | None = None,
        wake_text: str = "",
    ) -> None:
        if not self.settings.diagnostic:
            return
        now = time.monotonic()
        if now - self._last_level_log < self.settings.level_log_interval_ms / 1_000:
            return
        self._last_level_log = now
        wake_detail = ""
        if wake_score is not None:
            wake_detail = f" wake_score={wake_score:.3f}/{self.settings.wake_threshold:.3f} vosk={wake_text!r}"
        LOGGER.info(
            "INPUT level: RMS=%.4f (%.1f dBFS) peak=%.4f (%.1f dBFS) VAD=%s%s",
            rms,
            dbfs(rms),
            peak,
            dbfs(peak),
            "SUPPRESSED" if vad_speech is None else ("SPEECH" if vad_speech else "SILENCE"),
            wake_detail,
        )

    def _is_speech(self, frame: bytes) -> bool:
        # WebRTC VAD accepts only 10/20/30 ms, so split the 80 ms wake-word frame.
        subframe_bytes = SAMPLE_RATE * 20 // 1_000 * 2
        votes = [
            self._vad.is_speech(frame[index:index + subframe_bytes], SAMPLE_RATE)
            for index in range(0, len(frame), subframe_bytes)
        ]
        return sum(votes) >= 2

    def _noise_floor(self) -> float:
        if not self._noise_history:
            return 0.0
        return float(np.median(np.asarray(self._noise_history, dtype=np.float32)))

    def _energy_threshold(self) -> float:
        noise = self._noise_floor()
        return max(
            self.settings.min_audio_rms,
            noise * self.settings.vad_energy_ratio,
            noise + self.settings.vad_energy_delta,
        )

    def _ukrainian_wake_text(self, frame: bytes) -> str:
        if self._vosk_recognizer is None:
            return ""
        if self._vosk_recognizer.AcceptWaveform(frame):
            payload = json.loads(self._vosk_recognizer.Result())
            return str(payload.get("text", ""))
        payload = json.loads(self._vosk_recognizer.PartialResult())
        return str(payload.get("partial", ""))

    def _reset_capture(self) -> None:
        self._capture = []
        self._speech_seen = False
        self._silence_chunks = 0
        self._speech_chunks = 0
        self._consecutive_speech_chunks = 0
        self._interrupt_sent = False
        self._capture_started_at = None
        self._vad_start_run = 0
        self._vad_resume_run = 0

    def _transcribe(self, raw_frames: list[bytes], whisper_frames: list[bytes], speech_ms: int) -> None:
        try:
            raw_pcm = b"".join(raw_frames)
            pcm = b"".join(whisper_frames)
            self._save_diagnostic_wavs(raw_pcm, None)
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
            if speech_ms < self.settings.min_speech_ms or rms < self.settings.min_audio_rms:
                LOGGER.info(
                    "Ignoring short/quiet audio before Whisper: voiced=%d ms (min=%d), RMS=%.4f/%.1f dBFS (min=%.4f)",
                    speech_ms,
                    self.settings.min_speech_ms,
                    rms,
                    dbfs(rms),
                    self.settings.min_audio_rms,
                )
                return
            wav = pcm_to_wav(pcm)
            self._save_diagnostic_wavs(raw_pcm, pcm)
            selected_model = (
                self.settings.fast_whisper_model
                if speech_ms <= self.settings.fast_stt_max_speech_ms
                else self.settings.whisper_model
            )
            LOGGER.info(
                "Whisper SEND: model=%s language=%s format=%s audio=%d ms RMS=%.4f (%.1f dBFS)",
                selected_model,
                self.settings.language,
                self.settings.stt_response_format,
                len(whisper_frames) * FRAME_MS,
                rms,
                dbfs(rms),
            )
            models = [selected_model]
            if selected_model != self.settings.whisper_model:
                models.append(self.settings.whisper_model)
            response = None
            for attempt, model in enumerate(models):
                try:
                    response = requests.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {self.settings.groq_api_key}"},
                        files={"file": ("utterance.wav", wav, "audio/wav")},
                        data={
                            "model": model,
                            "language": self.settings.language,
                            "response_format": self.settings.stt_response_format,
                            "timestamp_granularities[]": "segment",
                            "temperature": "0",
                            "prompt": self.settings.stt_prompt,
                        },
                        timeout=60,
                    )
                    response.raise_for_status()
                    selected_model = model
                    break
                except requests.RequestException:
                    if attempt + 1 == len(models):
                        raise
                    LOGGER.warning(
                        "Fast Whisper model %s failed; retrying once with %s",
                        model,
                        self.settings.whisper_model,
                        exc_info=True,
                    )
            assert response is not None
            payload = response.json()
            text = str(payload.get("text", "")).strip()
            stt_done_unix_ms = int(time.time() * 1_000)
            metadata = {key: value for key, value in payload.items() if key != "text"}
            rejected_reason = None
            if speech_ms < 1_000 and is_common_whisper_hallucination(text):
                rejected_reason = "common_whisper_hallucination_on_low_voiced_audio"
            self._last_transcript = {
                "text": text,
                "metadata": metadata,
                "received_at": time.time(),
                "rejected_reason": rejected_reason,
                "model": selected_model,
            }
            LOGGER.info("Whisper transcript EXACT: %r", text)
            LOGGER.info("Whisper metadata: %s", json.dumps(metadata, ensure_ascii=False, separators=(",", ":")))
            if rejected_reason:
                LOGGER.warning("Transcript rejected: %s", rejected_reason)
            elif text:
                self._emit({
                    "type": "transcript",
                    "text": text,
                    "mic_end_unix_ms": self._last_mic_end_unix_ms,
                    "stt_done_unix_ms": stt_done_unix_ms,
                })
        except Exception as error:
            LOGGER.exception("Groq Whisper request failed")
            self._emit({"type": "error", "message": f"Groq Whisper не відповів: {error}"})
        finally:
            with self._lock:
                self._stt_busy = False
            LOGGER.info("Capture resumed after Whisper request")

    def _save_diagnostic_wavs(self, raw_pcm: bytes, whisper_pcm: bytes | None) -> None:
        if not self.settings.diagnostic:
            return
        self.settings.diagnostic_dir.mkdir(parents=True, exist_ok=True)
        raw_path = self.settings.diagnostic_dir / "last_raw.wav"
        whisper_path = self.settings.diagnostic_dir / "last_whisper.wav"
        raw_path.write_bytes(pcm_to_wav(raw_pcm))
        if whisper_pcm is None:
            whisper_path.unlink(missing_ok=True)
            LOGGER.info("Diagnostic WAV saved: raw=%s; Whisper WAV not created (audio was filtered)", raw_path)
            return
        whisper_path.write_bytes(pcm_to_wav(whisper_pcm))
        self._utterance_number += 1
        archive_dir = self.settings.diagnostic_dir / "history"
        archive_dir.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(raw_path, archive_dir / f"{stamp}-{self._utterance_number:03d}-raw.wav")
        shutil.copyfile(whisper_path, archive_dir / f"{stamp}-{self._utterance_number:03d}-whisper.wav")
        LOGGER.info("Diagnostic WAVs saved: raw=%s whisper=%s", raw_path, whisper_path)

    def analyze_wake_wav(self, path: Path, threshold: float | None = None) -> dict[str, object]:
        path = path.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"WAV not found: {path}")
        pcm = read_pcm_wav(path)
        used_threshold = self.settings.wake_threshold if threshold is None else max(0.0, min(1.0, threshold))
        scores: list[float] = []
        vosk_texts: list[str] = []
        detected_at_ms: int | None = None
        with self._wake_lock:
            if hasattr(self._wake_model, "reset"):
                self._wake_model.reset()
            if self._vosk_recognizer is not None:
                self._vosk_recognizer.Reset()
            for offset in range(0, len(pcm), FRAME_SAMPLES * 2):
                frame = pcm[offset:offset + FRAME_SAMPLES * 2]
                if len(frame) < FRAME_SAMPLES * 2:
                    frame += b"\0" * (FRAME_SAMPLES * 2 - len(frame))
                samples = np.frombuffer(frame, dtype=np.int16)
                predictions = self._wake_model.predict(samples)
                score = float(predictions.get(self._wake_model_name, 0.0))
                scores.append(score)
                vosk_text = self._ukrainian_wake_text(frame)
                if vosk_text and (not vosk_texts or vosk_text != vosk_texts[-1]):
                    vosk_texts.append(vosk_text)
                if detected_at_ms is None and (
                    score >= used_threshold
                    or contains_wake_word(
                        vosk_text,
                        self.settings.wake_variants,
                        self.settings.wake_vosk_max_edit_distance,
                    )
                ):
                    detected_at_ms = offset // 2 * 1_000 // SAMPLE_RATE
            if self._vosk_recognizer is not None:
                final_text = str(json.loads(self._vosk_recognizer.FinalResult()).get("text", ""))
                if final_text and (not vosk_texts or final_text != vosk_texts[-1]):
                    vosk_texts.append(final_text)
                self._vosk_recognizer.Reset()
            if hasattr(self._wake_model, "reset"):
                self._wake_model.reset()
        max_score = max(scores, default=0.0)
        top_scores = sorted(
            ({"time_ms": index * FRAME_MS, "score": round(score, 6)} for index, score in enumerate(scores)),
            key=lambda item: item["score"],
            reverse=True,
        )[:10]
        result = {
            "path": str(path),
            "duration_ms": len(pcm) // 2 * 1_000 // SAMPLE_RATE,
            "threshold": used_threshold,
            "configured_vosk_variants": self.settings.wake_variants,
            "max_openwakeword_score": round(max_score, 6),
            "top_scores": top_scores,
            "vosk_hypotheses": vosk_texts,
            "wake_detected": detected_at_ms is not None,
            "detected_at_ms": detected_at_ms,
        }
        LOGGER.info("Wake WAV measurement: %s", json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return result

    def _emit(self, event: dict[str, object]) -> None:
        try:
            self.events.put_nowait(event)
        except queue.Full:
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            self.events.put_nowait(event)

    def _clear_events(self) -> None:
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return


def pcm_to_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return output.getvalue()


def read_pcm_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        if wav.getframerate() != SAMPLE_RATE or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError(
                f"Expected 16 kHz, mono, 16-bit PCM WAV; got {wav.getframerate()} Hz, "
                f"{wav.getnchannels()} channel(s), {wav.getsampwidth() * 8}-bit"
            )
        return wav.readframes(wav.getnframes())


def frames_for_ms(duration_ms: int, minimum: int = 0) -> int:
    """Convert milliseconds to whole frames without shortening a configured interval."""
    if duration_ms <= 0:
        return minimum
    return max(minimum, (duration_ms + FRAME_MS - 1) // FRAME_MS)


def _bounded_edit_distance(left: str, right: str, limit: int) -> int:
    """Return Levenshtein distance, stopping once it cannot be within limit."""
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        row_min = row
        for column, right_char in enumerate(right, start=1):
            value = min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_char != right_char),
            )
            current.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1]


def contains_wake_word(
    text: str,
    variants: tuple[str, ...] = ("джарвіс", "джарвис", "джарвиз"),
    max_edit_distance: int = 1,
) -> bool:
    def normalize(value: str) -> str:
        value = value.casefold().replace("'", "").replace("’", "").replace("і", "и")
        return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()

    words = normalize(text).split()
    normalized_variants = [normalize(variant) for variant in variants if normalize(variant)]
    allowed_distance = max(0, max_edit_distance)
    return any(
        _bounded_edit_distance(word, variant, allowed_distance) <= allowed_distance
        for word in words
        for variant in normalized_variants
    )


def is_common_whisper_hallucination(text: str) -> bool:
    normalized = re.sub(r"[^\w]+", " ", text.casefold(), flags=re.UNICODE).strip()
    return normalized in {
        "дякую за перегляд",
        "продовження буде",
        "субтитри зроблені спільнотою amara org",
    }


settings = Settings.from_environment()
engine = VoiceInputEngine(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await run_in_threadpool(engine.start)
    yield
    await run_in_threadpool(engine.stop)


app = FastAPI(title="JARVIS Voice Input Core", version="0.2.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", **engine.get_state()}


@app.get("/devices")
def devices() -> dict[str, object]:
    return {"selected": engine.get_state()["device"], "input_devices": engine.available_input_devices()}


@app.get("/diagnostics")
def diagnostics() -> dict[str, object]:
    return {
        **engine.get_state(),
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "chunk_samples": FRAME_SAMPLES,
        "chunk_ms": FRAME_MS,
        "vad_aggressiveness": settings.vad_aggressiveness,
        "vad_energy_ratio": settings.vad_energy_ratio,
        "vad_energy_delta": settings.vad_energy_delta,
        "vad_start_chunks": settings.vad_start_chunks,
        "vad_resume_chunks": settings.vad_resume_chunks,
        "pre_roll_ms": settings.pre_roll_ms,
        "post_roll_ms": settings.post_roll_ms,
        "end_silence_ms": settings.silence_ms,
        "fast_end_silence_ms": settings.fast_silence_ms,
        "long_utterance_threshold_ms": settings.long_utterance_threshold_ms,
        "fast_whisper_model": settings.fast_whisper_model,
        "long_whisper_model": settings.whisper_model,
        "fast_stt_max_speech_ms": settings.fast_stt_max_speech_ms,
        "wake_threshold": settings.wake_threshold,
        "wake_variants": settings.wake_variants,
        "wake_vosk_max_edit_distance": settings.wake_vosk_max_edit_distance,
        "barge_in_enabled": settings.barge_in_enabled,
        "activation_mode": settings.activation_mode,
        "hotkey": "Ctrl+Alt+J" if settings.activation_mode == "hotkey" else None,
        "last_raw_wav": str(settings.diagnostic_dir / "last_raw.wav"),
        "last_whisper_wav": str(settings.diagnostic_dir / "last_whisper.wav"),
    }


@app.post("/state")
def set_state(request: StateRequest) -> dict[str, object]:
    engine.set_state(request.conversation_active, request.speaking)
    return {"status": "ok", **engine.get_state()}


@app.get("/events/next")
async def next_event(timeout: float = 30.0) -> dict[str, object]:
    """Cancellation-friendly long poll so Ctrl+C can stop Uvicorn cleanly."""
    deadline = time.monotonic() + max(0.1, min(timeout, 30.0))
    while time.monotonic() < deadline:
        try:
            return engine.events.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.1)
    return {"type": "timeout"}


@app.post("/stop")
def emergency_stop() -> dict[str, str]:
    engine.stop_listening()
    return {"status": "stopped"}


@app.post("/activate")
def manual_activate() -> dict[str, str]:
    engine.force_activate()
    return {"status": "activated"}


@app.post("/diagnostic/wake-wav")
async def diagnostic_wake_wav(request: WakeWavRequest) -> dict[str, object]:
    try:
        return await run_in_threadpool(engine.analyze_wake_wav, Path(request.path), request.threshold)
    except (FileNotFoundError, ValueError, wave.Error) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
