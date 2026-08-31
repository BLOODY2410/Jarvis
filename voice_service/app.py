from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import threading
import time
import urllib.request
import wave
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Literal, TypeAlias

import numpy as np
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pedalboard import Compressor, Gain, HighpassFilter, HighShelfFilter, Limiter, LowShelfFilter, PeakFilter, Pedalboard, Reverb, time_stretch
from piper import PiperVoice, SynthesisConfig
from pydantic import BaseModel, Field

logging.basicConfig(level=os.getenv("JARVIS_TTS_LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("jarvis.voice")
SERVICE_ROOT = Path(__file__).resolve().parent
load_dotenv(SERVICE_ROOT.parent / ".env")
MODEL_ID = "uk_UA-mykyta-high"
MODEL_FILENAME = f"{MODEL_ID}.onnx"
MODEL_SHA256 = "081d253cd246d7d4d698c6dd147b74cad498dafd213527887a3a490519138243"
MODEL_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/uk/uk_UA/mykyta/high"
VoiceMode: TypeAlias = Literal["raw", "jarvis", "jarvis_reference"]
ProviderMode: TypeAlias = Literal["auto", "fish", "piper"]
ACKNOWLEDGEMENTS = {
    "done": "Готово.",
    "opened": "Відкрито.",
    "closed": "Програму закрито.",
    "louder": "Гучність збільшено.",
    "quieter": "Гучність зменшено.",
    "muted": "Звук вимкнено.",
}


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
    provider: ProviderMode | None = None


@dataclass(frozen=True)
class VoiceSettings:
    model_dir: Path
    speed: float
    sample_rate: int | None
    default_mode: VoiceMode
    fx_enabled: bool
    provider: ProviderMode
    cache_dir: Path
    cache_max_chars: int

    @classmethod
    def from_environment(cls) -> "VoiceSettings":
        mode = os.getenv("JARVIS_TTS_MODE", "jarvis_reference").strip().lower()
        if mode not in {"raw", "jarvis", "jarvis_reference"}:
            raise ValueError("JARVIS_TTS_MODE must be 'raw', 'jarvis', or 'jarvis_reference'")
        sample_rate_text = os.getenv("JARVIS_TTS_SAMPLE_RATE", "native").strip().lower()
        sample_rate = None if sample_rate_text in {"", "native"} else int(sample_rate_text)
        if sample_rate is not None and not 8_000 <= sample_rate <= 48_000:
            raise ValueError("JARVIS_TTS_SAMPLE_RATE must be native or 8000..48000")
        provider = os.getenv("JARVIS_TTS_PROVIDER", "auto").strip().lower()
        if provider not in {"auto", "fish", "piper"}:
            raise ValueError("JARVIS_TTS_PROVIDER must be 'auto', 'fish', or 'piper'")
        return cls(
            model_dir=Path(os.getenv("JARVIS_TTS_MODEL_DIR", str(Path(__file__).parent / "models"))),
            speed=float(os.getenv("JARVIS_TTS_SPEED", "1.03")),
            sample_rate=sample_rate,
            default_mode=mode,  # type: ignore[arg-type]
            fx_enabled=env_bool("JARVIS_FX_ENABLED", True),
            provider=provider,  # type: ignore[arg-type]
            cache_dir=Path(os.getenv("JARVIS_TTS_CACHE_DIR", str(SERVICE_ROOT / "cache"))),
            cache_max_chars=int(os.getenv("JARVIS_TTS_CACHE_MAX_CHARS", "120")),
        )


@dataclass(frozen=True)
class FishAudioSettings:
    api_key: str
    reference_id: str
    model: str
    fallback_model: str
    endpoint: str
    latency: str
    connect_timeout: float
    read_timeout: float
    retries: int
    retry_delay: float
    speed: float
    fx_enabled: bool
    allow_paid_fallback: bool = False

    @classmethod
    def from_environment(cls) -> "FishAudioSettings":
        latency = os.getenv("FISH_AUDIO_LATENCY", "low").strip().lower()
        if latency not in {"low", "balanced", "normal"}:
            raise ValueError("FISH_AUDIO_LATENCY must be low, balanced, or normal")
        speed = float(os.getenv("FISH_AUDIO_SPEED", "0.97"))
        if not 0.90 <= speed <= 1.10:
            raise ValueError("FISH_AUDIO_SPEED must be between 0.90 and 1.10")
        return cls(
            api_key=os.getenv("FISH_AUDIO_API_KEY", "").strip(),
            reference_id=(os.getenv("FISH_AUDIO_REFERENCE_ID") or os.getenv("FISH_AUDIO_VOICE_ID") or "").strip(),
            model=os.getenv("FISH_AUDIO_MODEL", "s2.1-pro-free").strip() or "s2.1-pro-free",
            fallback_model=os.getenv("FISH_AUDIO_FALLBACK_MODEL", "s2-pro").strip() or "s2-pro",
            endpoint=os.getenv("FISH_AUDIO_TTS_URL", "https://api.fish.audio/v1/tts").strip(),
            latency=latency,
            connect_timeout=float(os.getenv("FISH_AUDIO_CONNECT_TIMEOUT_SECS", "2.5")),
            read_timeout=float(os.getenv("FISH_AUDIO_READ_TIMEOUT_SECS", "12")),
            retries=max(0, int(os.getenv("FISH_AUDIO_RETRIES", "1"))),
            retry_delay=max(0.0, float(os.getenv("FISH_AUDIO_RETRY_DELAY_SECS", "0.25"))),
            speed=speed,
            fx_enabled=env_bool("FISH_AUDIO_FX_ENABLED", False),
            allow_paid_fallback=env_bool("FISH_AUDIO_ALLOW_PAID_FALLBACK", False),
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.reference_id)

    @property
    def unavailable_reason(self) -> str | None:
        if not self.api_key:
            return "missing_api_key"
        if not self.reference_id:
            return "missing_reference_id"
        return None


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    provider: str
    model: str
    reference: str | None
    mode: str
    sample_rate: int
    cache_hit: bool = False
    fallback_from: str | None = None


class FishAudioError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
        self._load_lock = threading.Lock()
        self._ready = threading.Event()
        self._load_error: BaseException | None = None

    def load(self) -> None:
        if self._ready.is_set():
            return
        with self._load_lock:
            if self._ready.is_set():
                return
            try:
                model_path = ensure_model(self.settings.model_dir)
                LOGGER.info("Loading Piper voice %s...", model_path)
                self.voice = PiperVoice.load(str(model_path), use_cuda=False)
                self.native_sample_rate = int(self.voice.config.sample_rate)
                LOGGER.info("Piper %s loaded at %d Hz", MODEL_ID, self.native_sample_rate)
            except BaseException as error:
                self._load_error = error
                raise
            finally:
                self._ready.set()

    def ensure_loaded(self) -> None:
        if self.voice is not None:
            self._ready.set()
            return
        if not self._ready.is_set():
            self.load()
        if self._load_error is not None:
            raise RuntimeError(f"Piper fallback failed to load: {safe_error(self._load_error)}") from self._load_error
        if self.voice is None:
            raise RuntimeError("Piper fallback is not ready")

    def synthesize(self, request: SynthesisRequest) -> tuple[bytes, str, int]:
        self.ensure_loaded()
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


class FishAudioService:
    """Low-latency Fish Audio client. The API key is never exposed in status or logs."""

    _MODEL_REJECTION_STATUS = {400, 422}

    def __init__(self, settings: FishAudioSettings, fx: JarvisFx) -> None:
        self.settings = settings
        self.fx = fx
        self.effective_model = settings.model
        self._session = requests.Session()
        self._model_lock = threading.Lock()

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if not self.settings.configured:
            raise FishAudioError(self.settings.unavailable_reason or "fish_not_configured")

        model = self.effective_model
        try:
            audio = self._request_audio(request, model)
        except FishAudioError as error:
            # The free model is time-limited. If Fish explicitly rejects that model,
            # retry once with the current model recommended by the official API docs.
            if (
                error.status_code in self._MODEL_REJECTION_STATUS
                and model == "s2.1-pro-free"
                and self.settings.fallback_model
                and self.settings.fallback_model != model
                and self.settings.allow_paid_fallback
            ):
                audio = self._request_audio(request, self.settings.fallback_model)
                with self._model_lock:
                    self.effective_model = self.settings.fallback_model
                model = self.settings.fallback_model
            else:
                raise

        sample_rate = wav_sample_rate(audio)
        mode = "raw"
        if self.settings.fx_enabled:
            decoded, sample_rate = decode_wav(audio)
            audio = encode_wav(self.fx.process(decoded, sample_rate, 1.0), sample_rate)
            mode = "fish_fx"
        return SynthesisResult(
            audio=audio,
            provider="fish",
            model=model,
            reference=masked_reference(self.settings.reference_id),
            mode=mode,
            sample_rate=sample_rate,
        )

    def _request_audio(self, request: SynthesisRequest, model: str) -> bytes:
        payload = self._payload(request, streaming=False)
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/wav",
            "model": model,
        }
        attempts = self.settings.retries + 1
        last_error: FishAudioError | None = None
        for attempt in range(attempts):
            try:
                with self._session.post(
                    self.settings.endpoint,
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=(self.settings.connect_timeout, self.settings.read_timeout),
                ) as response:
                    if response.status_code != 200:
                        error = FishAudioError(
                            f"Fish Audio returned HTTP {response.status_code}",
                            response.status_code,
                        )
                        retryable = response.status_code == 429 or response.status_code >= 500
                        if not retryable or attempt + 1 >= attempts:
                            raise error
                        last_error = error
                    else:
                        chunks = bytearray()
                        for chunk in response.iter_content(chunk_size=16 * 1024):
                            if chunk:
                                chunks.extend(chunk)
                        audio = bytes(chunks)
                        try:
                            validate_wav(audio)
                        except (ValueError, wave.Error) as error:
                            raise FishAudioError("Fish Audio returned invalid WAV audio") from error
                        return audio
            except requests.RequestException as error:
                last_error = FishAudioError(f"Fish Audio network failure: {type(error).__name__}")
                if attempt + 1 >= attempts:
                    raise last_error from error
            if self.settings.retry_delay:
                time.sleep(self.settings.retry_delay * (attempt + 1))
        raise last_error or FishAudioError("Fish Audio request failed")

    def open_pcm_stream(self, request: SynthesisRequest) -> tuple[requests.Response, str]:
        """Open Fish's response without consuming it; caller owns the response."""
        if not self.settings.configured:
            raise FishAudioError(self.settings.unavailable_reason or "fish_not_configured")
        model = self.effective_model
        response = self._open_response(request, model)
        if response.status_code == 200:
            return response, model
        status = response.status_code
        response.close()
        if (
            status in self._MODEL_REJECTION_STATUS
            and model == "s2.1-pro-free"
            and self.settings.allow_paid_fallback
            and self.settings.fallback_model
            and self.settings.fallback_model != model
        ):
            model = self.settings.fallback_model
            response = self._open_response(request, model)
            if response.status_code == 200:
                with self._model_lock:
                    self.effective_model = model
                return response, model
            status = response.status_code
            response.close()
        raise FishAudioError(f"Fish Audio returned HTTP {status}", status)

    def _open_response(self, request: SynthesisRequest, model: str) -> requests.Response:
        payload = self._payload(request, streaming=True)
        return self._session.post(
            self.settings.endpoint,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "audio/wav",
                "model": model,
            },
            json=payload,
            stream=True,
            timeout=(self.settings.connect_timeout, self.settings.read_timeout),
        )

    def _payload(self, request: SynthesisRequest, streaming: bool) -> dict[str, object]:
        payload: dict[str, object] = {
            "text": request.text.strip(),
            "reference_id": self.settings.reference_id,
            "format": "wav",
            "latency": self.settings.latency,
            "prosody": {
                "speed": request.speed if request.speed is not None else self.settings.speed,
                "volume": 0,
                "normalize_loudness": True,
            },
        }
        if streaming:
            payload["sample_rate"] = request.sample_rate or 44_100
        elif request.sample_rate in {8_000, 16_000, 24_000, 32_000, 44_100}:
            # Fish accepts only a fixed set of WAV sample rates. Omit unsupported
            # values and let the API use its documented 44.1 kHz default.
            payload["sample_rate"] = request.sample_rate
        return payload


class AudioCache:
    def __init__(self, root: Path, max_chars: int) -> None:
        self.root = root
        self.max_chars = max_chars
        self.root.mkdir(parents=True, exist_ok=True)

    def key(
        self,
        provider: str,
        model: str,
        reference: str,
        request: SynthesisRequest,
        default_speed: float | None = None,
    ) -> str | None:
        text = " ".join(request.text.split())
        if not text or len(text) > self.max_chars:
            return None
        material = "\n".join([
            "cinematic-v3", provider, model, reference, text,
            str(request.speed if request.speed is not None else default_speed or "default"),
            str(request.sample_rate or "native"),
        ])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def read(self, key: str | None) -> bytes | None:
        if key is None:
            return None
        path = self.root / f"{key}.wav"
        try:
            audio = path.read_bytes()
            validate_wav(audio)
            return audio
        except (FileNotFoundError, OSError, ValueError, wave.Error):
            return None

    def write(self, key: str | None, audio: bytes) -> None:
        if key is None:
            return
        path = self.root / f"{key}.wav"
        temporary = path.with_suffix(".wav.tmp")
        temporary.write_bytes(audio)
        temporary.replace(path)


class VoiceRouter:
    def __init__(
        self,
        settings: VoiceSettings,
        fish: FishAudioService,
        piper: PiperVoiceService,
        cache: AudioCache,
    ) -> None:
        self.settings = settings
        self.fish = fish
        self.piper = piper
        self.cache = cache
        self.last_provider = "piper"
        self.last_error: str | None = None
        self._state_lock = threading.Lock()

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        requested = request.provider or self.settings.provider
        use_fish = requested in {"auto", "fish"} and self.fish.settings.configured
        if use_fish:
            model = self.fish.effective_model
            fish_cache_provider = "fish_fx" if self.fish.settings.fx_enabled else "fish"
            cache_key = self.cache.key(
                fish_cache_provider,
                model,
                self.fish.settings.reference_id,
                request,
                self.fish.settings.speed,
            )
            cached = self.cache.read(cache_key)
            if cached is not None:
                result = SynthesisResult(
                    cached, "fish", model, masked_reference(self.fish.settings.reference_id),
                    "fish_fx" if self.fish.settings.fx_enabled else "raw",
                    wav_sample_rate(cached), cache_hit=True,
                )
                self._remember(result.provider, None)
                return result
            try:
                result = self.fish.synthesize(request)
                # The negotiated model can differ when the free model has expired.
                final_key = self.cache.key(
                    fish_cache_provider,
                    result.model,
                    self.fish.settings.reference_id,
                    request,
                    self.fish.settings.speed,
                )
                self.cache.write(final_key, result.audio)
                self._remember(result.provider, None)
                return result
            except FishAudioError as error:
                self._remember("piper", safe_error(error))
                LOGGER.warning("Fish Audio unavailable; using local Piper fallback (%s)", safe_error(error))
                return self._piper_result(request, fallback_from="fish")
        if requested == "fish" and not self.fish.settings.configured:
            self._remember("piper", self.fish.settings.unavailable_reason)
        return self._piper_result(request, fallback_from="fish" if requested == "fish" else None)

    def _piper_result(self, request: SynthesisRequest, fallback_from: str | None = None) -> SynthesisResult:
        self.piper.ensure_loaded()
        audio, mode, sample_rate = self.piper.synthesize(request)
        result = SynthesisResult(
            audio, "piper", MODEL_ID, "mykyta", mode, sample_rate,
            fallback_from=fallback_from,
        )
        self._remember("piper", self.last_error if fallback_from else None)
        return result

    def _remember(self, provider: str, error: str | None) -> None:
        with self._state_lock:
            self.last_provider = provider
            self.last_error = error

    def prewarm(self) -> None:
        if self.settings.provider not in {"auto", "fish"} or not self.fish.settings.configured:
            return
        for text in ACKNOWLEDGEMENTS.values():
            try:
                self.synthesize(SynthesisRequest(text=text, provider="fish"))
            except Exception as error:
                LOGGER.warning("Fish Audio cache prewarm stopped (%s)", safe_error(error))
                break


def validate_wav(audio: bytes) -> None:
    if len(audio) <= 44 or audio[:4] not in {b"RIFF", b"RF64"} or audio[8:12] != b"WAVE":
        raise ValueError("TTS provider returned invalid WAV audio")
    with wave.open(io.BytesIO(audio), "rb") as wav:
        if wav.getnframes() <= 0:
            raise ValueError("TTS provider returned an empty WAV")


def wav_sample_rate(audio: bytes) -> int:
    with wave.open(io.BytesIO(audio), "rb") as wav:
        return wav.getframerate()


def decode_wav(audio: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(audio), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError("Only 16-bit PCM WAV is supported for optional Fish FX")
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        frames = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        frames = frames.reshape(-1, channels).mean(axis=1)
    return frames, sample_rate


def masked_reference(reference_id: str) -> str | None:
    if not reference_id:
        return None
    if len(reference_id) <= 8:
        return "configured"
    return f"{reference_id[:4]}...{reference_id[-4:]}"


def safe_error(error: BaseException) -> str:
    if isinstance(error, FishAudioError) and error.status_code is not None:
        return f"http_{error.status_code}"
    return type(error).__name__


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
fish_settings = FishAudioSettings.from_environment()
fx_settings = FxSettings.from_environment()
fx = JarvisFx(fx_settings)
piper = PiperVoiceService(settings, fx)
fish = FishAudioService(fish_settings, fx)
voice = VoiceRouter(settings, fish, piper, AudioCache(settings.cache_dir, settings.cache_max_chars))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fish-primary readiness does not wait for the heavy local model. Piper is
    # prepared in parallel and ensure_loaded() safely waits if an early fallback
    # reaches it first.
    threading.Thread(target=piper.load, name="piper-lazy-load", daemon=True).start()
    threading.Thread(target=voice.prewarm, name="fish-cache-prewarm", daemon=True).start()
    yield


app = FastAPI(title="JARVIS Voice Core", version="0.4.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    configured_provider = settings.provider
    preferred_provider = "fish" if configured_provider == "auto" and fish_settings.configured else configured_provider
    return {
        "service": "jarvis_voice",
        "status": "ready" if preferred_provider == "fish" or piper.voice is not None else "loading",
        "engine": preferred_provider,
        "provider_mode": configured_provider,
        "active_provider": voice.last_provider,
        "model": fish.effective_model if preferred_provider == "fish" else MODEL_ID,
        "reference": masked_reference(fish_settings.reference_id) if preferred_provider == "fish" else "mykyta",
        "sample_rate": 44_100 if preferred_provider == "fish" else settings.sample_rate or piper.native_sample_rate,
        "speed": fish_settings.speed if preferred_provider == "fish" else settings.speed,
        "mode": ("fish_fx" if fish_settings.fx_enabled else "raw") if preferred_provider == "fish" else settings.default_mode,
        "fx_enabled": fish_settings.fx_enabled if preferred_provider == "fish" else settings.fx_enabled,
        "presets": ["auto", "fish", "piper"],
        "fish": {
            "configured": fish_settings.configured,
            "unavailable_reason": fish_settings.unavailable_reason,
            "model": fish.effective_model,
            "reference": masked_reference(fish_settings.reference_id),
            "latency": fish_settings.latency,
            "speed": fish_settings.speed,
            "fx_enabled": fish_settings.fx_enabled,
            "paid_fallback_allowed": fish_settings.allow_paid_fallback,
        },
        "piper": {"ready": piper.voice is not None, "model": MODEL_ID, "fx_enabled": settings.fx_enabled},
        "cache": {"directory": str(settings.cache_dir), "max_phrase_chars": settings.cache_max_chars},
        "last_fallback_reason": voice.last_error,
        "device": "cloud" if preferred_provider == "fish" else "cpu",
    }


@app.post("/synthesize")
async def synthesize(request: SynthesisRequest) -> Response:
    try:
        result = await run_in_threadpool(voice.synthesize, request)
    except Exception as error:
        LOGGER.exception("Synthesis failed")
        raise HTTPException(status_code=500, detail=safe_error(error)) from error
    return Response(content=result.audio, media_type="audio/wav", headers={
        "X-Jarvis-Engine": result.provider,
        "X-Jarvis-Provider": result.provider,
        "X-Jarvis-Model": result.model,
        "X-Jarvis-Speaker": result.reference or "default",
        "X-Jarvis-Reference": result.reference or "default",
        "X-Jarvis-Mode": result.mode,
        "X-Jarvis-Sample-Rate": str(result.sample_rate),
        "X-Jarvis-Cache": "hit" if result.cache_hit else "miss",
        "X-Jarvis-Fallback-From": result.fallback_from or "none",
    })


@app.get("/ack/{name}")
async def acknowledgement(name: str) -> Response:
    """Return only pre-generated Fish audio; never make a live cloud call."""
    text = ACKNOWLEDGEMENTS.get(name)
    if text is None:
        raise HTTPException(status_code=404, detail="unknown_acknowledgement")
    request = SynthesisRequest(text=text, provider="fish")
    provider = "fish_fx" if fish.settings.fx_enabled else "fish"
    key = voice.cache.key(
        provider,
        fish.effective_model,
        fish.settings.reference_id,
        request,
        fish.settings.speed,
    )
    audio = voice.cache.read(key)
    if audio is None:
        return Response(status_code=204, headers={"X-Jarvis-Cache": "miss"})
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"X-Jarvis-Cache": "hit", "X-Jarvis-Provider": "fish"},
    )


def wav_pcm_chunks(response: requests.Response) -> Iterator[bytes]:
    """Strip a streamed PCM WAV container and yield only its data chunk."""
    header = bytearray()
    data_remaining: int | None = None
    try:
        for chunk in response.iter_content(chunk_size=8 * 1024):
            if not chunk:
                continue
            if data_remaining is None:
                header.extend(chunk)
                marker = header.find(b"data")
                if marker < 0 or len(header) < marker + 8:
                    if len(header) > 64 * 1024:
                        raise FishAudioError("Fish Audio WAV header is too large")
                    continue
                data_remaining = int.from_bytes(header[marker + 4:marker + 8], "little")
                chunk = bytes(header[marker + 8:])
                header.clear()
            if data_remaining <= 0:
                break
            output = chunk[:data_remaining]
            data_remaining -= len(output)
            if output:
                yield output
    finally:
        response.close()


@app.post("/synthesize/stream")
async def synthesize_stream(request: SynthesisRequest) -> StreamingResponse:
    """Stream signed 16-bit mono PCM; the legacy WAV endpoint remains intact."""
    request.sample_rate = request.sample_rate or 44_100
    try:
        response, model = await run_in_threadpool(fish.open_pcm_stream, request)
        first_byte_ms = int(time.time() * 1_000)
        LOGGER.info("TTS stream headers ready: provider=fish model=%s first_byte_ms=%d", model, first_byte_ms)
        voice._remember("fish", None)
        return StreamingResponse(
            wav_pcm_chunks(response),
            media_type="audio/L16",
            headers={
                "X-Jarvis-Provider": "fish",
                "X-Jarvis-Model": model,
                "X-Jarvis-Sample-Rate": str(request.sample_rate),
                "X-Jarvis-Channels": "1",
                "X-Jarvis-Sample-Format": "s16le",
            },
        )
    except Exception as error:
        LOGGER.warning("Fish streaming unavailable; returning Piper PCM (%s)", safe_error(error))
        try:
            result = await run_in_threadpool(voice._piper_result, request, "fish")
            pcm, sample_rate = decode_wav(result.audio)
            raw = (np.clip(pcm, -1.0, 1.0) * 32767.0).round().astype("<i2").tobytes()
            return StreamingResponse(
                iter((raw,)),
                media_type="audio/L16",
                headers={
                    "X-Jarvis-Provider": "piper",
                    "X-Jarvis-Model": MODEL_ID,
                    "X-Jarvis-Sample-Rate": str(sample_rate),
                    "X-Jarvis-Channels": "1",
                    "X-Jarvis-Sample-Format": "s16le",
                    "X-Jarvis-Fallback-From": "fish",
                },
            )
        except Exception as fallback_error:
            raise HTTPException(status_code=503, detail=safe_error(fallback_error)) from fallback_error
