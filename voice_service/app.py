from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import threading
import urllib.request
import wave
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, TypeAlias

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pedalboard import Compressor, Gain, HighpassFilter, HighShelfFilter, Limiter, LowShelfFilter, PeakFilter, Pedalboard, Reverb, time_stretch
from piper import PiperVoice, SynthesisConfig
from pydantic import BaseModel, Field

logging.basicConfig(level=os.getenv("JARVIS_TTS_LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("jarvis.voice")
MODEL_ID = "uk_UA-mykyta-high"
MODEL_FILENAME = f"{MODEL_ID}.onnx"
MODEL_SHA256 = "081d253cd246d7d4d698c6dd147b74cad498dafd213527887a3a490519138243"
MODEL_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/uk/uk_UA/mykyta/high"
VoiceMode: TypeAlias = Literal["raw", "jarvis", "jarvis_reference"]


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() not in {"0", "false", "no", "off"}


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    speaker: str | None = None  # Kept for compatibility; Mykyta is single-speaker.
    sample_rate: int | None = Field(default=None, ge=8_000, le=48_000)
    speed: float | None = Field(default=None, ge=0.75, le=1.5)
    mode: VoiceMode | None = None
    fx_enabled: bool | None = None


@dataclass(frozen=True)
class VoiceSettings:
    model_dir: Path
    speed: float
    sample_rate: int | None
    default_mode: VoiceMode
    fx_enabled: bool

    @classmethod
    def from_environment(cls) -> "VoiceSettings":
        mode = os.getenv("JARVIS_TTS_MODE", "jarvis_reference").strip().lower()
        if mode not in {"raw", "jarvis", "jarvis_reference"}:
            raise ValueError("JARVIS_TTS_MODE must be 'raw', 'jarvis', or 'jarvis_reference'")
        sample_rate_text = os.getenv("JARVIS_TTS_SAMPLE_RATE", "native").strip().lower()
        sample_rate = None if sample_rate_text in {"", "native"} else int(sample_rate_text)
        if sample_rate is not None and not 8_000 <= sample_rate <= 48_000:
            raise ValueError("JARVIS_TTS_SAMPLE_RATE must be native or 8000..48000")
        return cls(
            model_dir=Path(os.getenv("JARVIS_TTS_MODEL_DIR", str(Path(__file__).parent / "models"))),
            speed=float(os.getenv("JARVIS_TTS_SPEED", "1.03")),
            sample_rate=sample_rate,
            default_mode=mode,  # type: ignore[arg-type]
            fx_enabled=env_bool("JARVIS_FX_ENABLED", True),
        )


@dataclass(frozen=True)
class FxSettings:
    pitch_semitones: float
    formant_shift_semitones: float
    preserve_formants: bool
    low_shelf_gain_db: float
    warmth_gain_db: float
    boxiness_gain_db: float
    presence_gain_db: float
    air_gain_db: float
    compressor_threshold_db: float
    compressor_ratio: float
    saturation_drive_db: float
    saturation_mix: float
    reverb_room_size: float
    reverb_wet_level: float
    spatial_delay_ms: float
    spatial_width: float
    output_gain_db: float

    @classmethod
    def from_environment(cls) -> "FxSettings":
        return cls(
            pitch_semitones=float(os.getenv("JARVIS_FX_PITCH_SEMITONES", "-1.10")),
            formant_shift_semitones=float(os.getenv("JARVIS_FX_FORMANT_SHIFT_SEMITONES", "-0.70")),
            preserve_formants=env_bool("JARVIS_FX_PRESERVE_FORMANTS", True),
            low_shelf_gain_db=float(os.getenv("JARVIS_FX_LOW_SHELF_DB", "2.4")),
            warmth_gain_db=float(os.getenv("JARVIS_FX_WARMTH_DB", "1.4")),
            boxiness_gain_db=float(os.getenv("JARVIS_FX_BOXINESS_DB", "-2.2")),
            presence_gain_db=float(os.getenv("JARVIS_FX_PRESENCE_DB", "2.3")),
            air_gain_db=float(os.getenv("JARVIS_FX_AIR_DB", "0.8")),
            compressor_threshold_db=float(os.getenv("JARVIS_FX_COMPRESSOR_THRESHOLD_DB", "-21.0")),
            compressor_ratio=float(os.getenv("JARVIS_FX_COMPRESSOR_RATIO", "3.0")),
            saturation_drive_db=float(os.getenv("JARVIS_FX_SATURATION_DRIVE_DB", "3.0")),
            saturation_mix=float(os.getenv("JARVIS_FX_SATURATION_MIX", "0.08")),
            reverb_room_size=float(os.getenv("JARVIS_FX_REVERB_ROOM_SIZE", "0.10")),
            reverb_wet_level=float(os.getenv("JARVIS_FX_REVERB_WET_LEVEL", "0.035")),
            spatial_delay_ms=float(os.getenv("JARVIS_FX_SPATIAL_DELAY_MS", "7.0")),
            spatial_width=float(os.getenv("JARVIS_FX_SPATIAL_WIDTH", "0.12")),
            output_gain_db=float(os.getenv("JARVIS_FX_OUTPUT_GAIN_DB", "-1.2")),
        )


class JarvisFx:
    def __init__(self, settings: FxSettings) -> None:
        self.settings = settings
        self._tone_board = Pedalboard([
            HighpassFilter(cutoff_frequency_hz=62.0),
            LowShelfFilter(cutoff_frequency_hz=175.0, gain_db=settings.low_shelf_gain_db, q=0.7),
            PeakFilter(cutoff_frequency_hz=330.0, gain_db=settings.warmth_gain_db, q=0.75),
            PeakFilter(cutoff_frequency_hz=760.0, gain_db=settings.boxiness_gain_db, q=1.0),
            PeakFilter(cutoff_frequency_hz=2_850.0, gain_db=settings.presence_gain_db, q=0.85),
            HighShelfFilter(cutoff_frequency_hz=6_200.0, gain_db=settings.air_gain_db, q=0.7),
            Compressor(threshold_db=settings.compressor_threshold_db, ratio=settings.compressor_ratio, attack_ms=8.0, release_ms=95.0),
            Gain(gain_db=settings.output_gain_db),
        ])
        self._space_board = Pedalboard([
            Reverb(room_size=settings.reverb_room_size, damping=0.88, wet_level=settings.reverb_wet_level, dry_level=1.0 - settings.reverb_wet_level, width=0.45),
            Limiter(threshold_db=-1.0, release_ms=75.0),
        ])

    def process(self, audio: np.ndarray, sample_rate: int, speed: float) -> np.ndarray:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        # Resampling moves pitch and vocal resonances together. Rubber Band then
        # restores only the requested fundamental pitch while preserving those
        # lowered resonances. This produces a subtle adult/assistant timbre
        # without training on or cloning a real person's voice.
        formant_factor = 2.0 ** (-self.settings.formant_shift_semitones / 12.0)
        formant_audio = resample_to_length(audio, max(1, round(audio.size * formant_factor)))
        processed = time_stretch(
            formant_audio, float(sample_rate),
            stretch_factor=speed * formant_factor,
            pitch_shift_in_semitones=self.settings.pitch_semitones - self.settings.formant_shift_semitones,
            high_quality=True,
            transient_mode="smooth",
            preserve_formants=self.settings.preserve_formants,
        )
        processed = np.asarray(processed, dtype=np.float32).reshape(-1)
        processed = soft_saturate(
            processed,
            drive_db=self.settings.saturation_drive_db,
            mix=self.settings.saturation_mix,
        )
        processed = self._tone_board(processed, float(sample_rate), reset=True)
        stereo = spatialize(
            np.asarray(processed, dtype=np.float32).reshape(-1),
            sample_rate,
            delay_ms=self.settings.spatial_delay_ms,
            width=self.settings.spatial_width,
        )
        processed = self._space_board(stereo, float(sample_rate), reset=True)
        return np.clip(np.asarray(processed, dtype=np.float32), -1.0, 1.0)


class PiperVoiceService:
    def __init__(self, settings: VoiceSettings, fx: JarvisFx) -> None:
        self.settings = settings
        self.fx = fx
        self.voice: PiperVoice | None = None
        self.native_sample_rate = 0
        self._lock = threading.Lock()

    def load(self) -> None:
        model_path = ensure_model(self.settings.model_dir)
        LOGGER.info("Loading Piper voice %s...", model_path)
        self.voice = PiperVoice.load(str(model_path), use_cuda=False)
        self.native_sample_rate = int(self.voice.config.sample_rate)
        LOGGER.info("Piper %s loaded at %d Hz", MODEL_ID, self.native_sample_rate)

    def synthesize(self, request: SynthesisRequest) -> tuple[bytes, str, int]:
        if self.voice is None:
            raise RuntimeError("The Piper voice is not loaded yet")
        speed = request.speed or self.settings.speed
        mode = request.mode or self.settings.default_mode
        use_fx = (self.settings.fx_enabled if request.fx_enabled is None else request.fx_enabled) and mode != "raw"
        # Piper length_scale is inverse speed. FX mode applies tempo in the FX stage.
        syn_config = SynthesisConfig(length_scale=1.0 / speed if not use_fx else 1.0)
        with self._lock:
            chunks = list(self.voice.synthesize(request.text.strip(), syn_config=syn_config))
        if not chunks:
            raise RuntimeError("Piper returned no audio")
        audio = np.concatenate([np.asarray(chunk.audio_float_array, dtype=np.float32).reshape(-1) for chunk in chunks])
        sample_rate = int(chunks[0].sample_rate)
        if use_fx:
            audio = self.fx.process(audio, sample_rate, speed)
        output_sample_rate = request.sample_rate or self.settings.sample_rate or sample_rate
        if output_sample_rate != sample_rate:
            audio = resample_linear(audio, sample_rate, output_sample_rate)
            sample_rate = output_sample_rate
        return encode_wav(audio, sample_rate), "jarvis_reference" if use_fx else "raw", sample_rate


def resample_to_length(audio: np.ndarray, target_length: int) -> np.ndarray:
    if audio.size < 2 or audio.size == target_length:
        return audio.astype(np.float32, copy=False)
    return np.interp(
        np.linspace(0.0, 1.0, num=target_length, endpoint=True),
        np.linspace(0.0, 1.0, num=audio.size, endpoint=True),
        audio,
    ).astype(np.float32)


def soft_saturate(audio: np.ndarray, drive_db: float, mix: float) -> np.ndarray:
    mix = float(np.clip(mix, 0.0, 1.0))
    drive = 10.0 ** (drive_db / 20.0)
    saturated = np.tanh(audio * drive) / max(np.tanh(drive), 1e-6)
    return ((1.0 - mix) * audio + mix * saturated).astype(np.float32)


def spatialize(audio: np.ndarray, sample_rate: int, delay_ms: float, width: float) -> np.ndarray:
    width = float(np.clip(width, 0.0, 0.35))
    if width == 0.0:
        return np.stack((audio, audio))
    left_delay = max(1, round(sample_rate * delay_ms / 1_000.0))
    right_delay = max(1, round(sample_rate * (delay_ms + 3.5) / 1_000.0))
    left_echo = np.pad(audio[:-left_delay], (left_delay, 0)) if audio.size > left_delay else np.zeros_like(audio)
    right_echo = np.pad(audio[:-right_delay], (right_delay, 0)) if audio.size > right_delay else np.zeros_like(audio)
    scale = 1.0 / (1.0 + width)
    return np.stack(((audio + width * left_echo) * scale, (audio + width * right_echo) * scale)).astype(np.float32)


def resample_linear(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if audio.size < 2 or source_rate == target_rate:
        return audio
    target_length = max(1, round(audio.shape[-1] * target_rate / source_rate))
    if audio.ndim == 1:
        return resample_to_length(audio, target_length)
    return np.stack([resample_to_length(channel, target_length) for channel in audio])


def encode_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        channels = 1
        frames = audio
    elif audio.ndim == 2:
        channels = audio.shape[0]
        frames = audio.T
    else:
        raise ValueError("Audio must be mono or channels-first stereo")
    pcm = (np.clip(frames, -1.0, 1.0) * 32767.0).round().astype("<i2").tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


def ensure_model(model_dir: Path) -> Path:
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / MODEL_FILENAME
    ensure_download(model_path, f"{MODEL_BASE_URL}/{MODEL_FILENAME}", 100_000_000, MODEL_SHA256)
    ensure_download(model_dir / f"{MODEL_FILENAME}.json", f"{MODEL_BASE_URL}/{MODEL_FILENAME}.json", 1_000)
    return model_path


def ensure_download(path: Path, url: str, min_size: int, expected_hash: str | None = None) -> None:
    if path.is_file():
        verify_file(path, min_size, expected_hash)
        return
    partial_path = path.with_suffix(path.suffix + ".download")
    try:
        LOGGER.info("Downloading %s", url)
        request = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Voice-Core/0.2"})
        with urllib.request.urlopen(request, timeout=60) as source, partial_path.open("wb") as target:
            shutil.copyfileobj(source, target)
        verify_file(partial_path, min_size, expected_hash)
        partial_path.replace(path)
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise


def verify_file(path: Path, min_size: int, expected_hash: str | None = None) -> None:
    if path.stat().st_size < min_size:
        raise RuntimeError(f"Downloaded file is too small: {path}")
    if expected_hash:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected_hash:
            raise RuntimeError(f"SHA-256 mismatch: {path}")


settings = VoiceSettings.from_environment()
fx_settings = FxSettings.from_environment()
voice = PiperVoiceService(settings, JarvisFx(fx_settings))


@asynccontextmanager
async def lifespan(_: FastAPI):
    await run_in_threadpool(voice.load)
    yield


app = FastAPI(title="JARVIS Voice Core", version="0.3.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ready" if voice.voice is not None else "loading",
        "engine": "piper", "model": MODEL_ID, "speaker": "mykyta",
        "sample_rate": settings.sample_rate or voice.native_sample_rate,
        "speed": settings.speed, "mode": settings.default_mode,
        "fx_enabled": settings.fx_enabled, "presets": ["raw", "jarvis_reference"],
        "fx": asdict(fx_settings), "device": "cpu",
    }


@app.post("/synthesize")
async def synthesize(request: SynthesisRequest) -> Response:
    try:
        wav, mode, sample_rate = await run_in_threadpool(voice.synthesize, request)
    except Exception as error:
        LOGGER.exception("Synthesis failed")
        raise HTTPException(status_code=500, detail=str(error)) from error
    return Response(content=wav, media_type="audio/wav", headers={
        "X-Jarvis-Engine": "piper", "X-Jarvis-Model": MODEL_ID,
        "X-Jarvis-Speaker": "mykyta", "X-Jarvis-Mode": mode,
        "X-Jarvis-Sample-Rate": str(sample_rate),
    })
