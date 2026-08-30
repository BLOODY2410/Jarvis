from __future__ import annotations

import io
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app import (
    AudioCache,
    FishAudioError,
    FishAudioSettings,
    FxSettings,
    JarvisFx,
    PiperVoiceService,
    SynthesisRequest,
    SynthesisResult,
    VoiceRouter,
    VoiceSettings,
    encode_wav,
)


class FakePiperVoice:
    def synthesize(self, _text: str, syn_config: object):
        sample_rate = 22_050
        duration = 0.8
        time = np.arange(round(sample_rate * duration), dtype=np.float32) / sample_rate
        signal = 0.13 * np.sin(2.0 * np.pi * 128.0 * time)
        signal += 0.035 * np.sin(2.0 * np.pi * 256.0 * time)
        return [SimpleNamespace(audio_float_array=signal, sample_rate=sample_rate)]


class VoiceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = VoiceSettings.from_environment()
        self.service = PiperVoiceService(
            replace(settings, default_mode="jarvis_reference", fx_enabled=True),
            JarvisFx(FxSettings.from_environment()),
        )
        self.service.voice = FakePiperVoice()  # type: ignore[assignment]

    def test_raw_mode_stays_mono(self) -> None:
        wav_bytes, mode, sample_rate = self.service.synthesize(
            SynthesisRequest(text="Перевірка чистого українського голосу.", mode="raw")
        )
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getframerate(), sample_rate)
            self.assertGreater(wav.getnframes(), 1_000)
        self.assertEqual(mode, "raw")


class FakeFishService:
    def __init__(self, settings: FishAudioSettings, failure: FishAudioError | None = None) -> None:
        self.settings = settings
        self.effective_model = settings.model
        self.failure = failure
        self.calls = 0

    def synthesize(self, _request: SynthesisRequest) -> SynthesisResult:
        self.calls += 1
        if self.failure:
            raise self.failure
        sample_rate = 44_100
        audio = encode_wav(np.zeros(sample_rate // 20, dtype=np.float32), sample_rate)
        return SynthesisResult(audio, "fish", self.effective_model, "abcd...wxyz", "raw", sample_rate)


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = replace(
            VoiceSettings.from_environment(),
            provider="auto",
            default_mode="raw",
            fx_enabled=False,
        )
        self.piper = PiperVoiceService(settings, JarvisFx(FxSettings.from_environment()))
        self.piper.voice = FakePiperVoice()  # type: ignore[assignment]
        self.service = PiperVoiceService(
            replace(settings, default_mode="jarvis_reference", fx_enabled=True),
            JarvisFx(FxSettings.from_environment()),
        )
        self.service.voice = FakePiperVoice()  # type: ignore[assignment]
        self.fish_settings = FishAudioSettings(
            api_key="test-key",
            reference_id="abcdefghijklmnopqrstuvwx",
            model="s2.1-pro-free",
            fallback_model="s2-pro",
            endpoint="https://api.fish.audio/v1/tts",
            latency="low",
            connect_timeout=0.1,
            read_timeout=0.1,
            retries=1,
            retry_delay=0,
            fx_enabled=False,
        )
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = settings

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_router(self, fish: FakeFishService) -> VoiceRouter:
        return VoiceRouter(
            self.settings,
            fish,  # type: ignore[arg-type]
            self.piper,
            AudioCache(Path(self.temp_dir.name), 120),
        )

    def test_short_fish_phrase_is_cached(self) -> None:
        fish = FakeFishService(self.fish_settings)
        router = self.make_router(fish)
        first = router.synthesize(SynthesisRequest(text="Готово."))
        second = router.synthesize(SynthesisRequest(text="Готово."))
        self.assertEqual(first.provider, "fish")
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(fish.calls, 1)

    def test_fish_failures_fall_back_to_piper(self) -> None:
        for status in (401, 403, 429, 500, None):
            with self.subTest(status=status):
                fish = FakeFishService(self.fish_settings, FishAudioError("unavailable", status))
                result = self.make_router(fish).synthesize(SynthesisRequest(text="Перевірка fallback."))
                self.assertEqual(result.provider, "piper")
                self.assertEqual(result.fallback_from, "fish")
                self.assertEqual(result.mode, "raw")

    def test_reference_preset_is_stereo_and_keeps_duration(self) -> None:
        wav_bytes, mode, sample_rate = self.service.synthesize(
            SynthesisRequest(text="Джарвіс готовий до роботи.", mode="jarvis_reference", speed=1.03)
        )
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 2)
            self.assertEqual(wav.getframerate(), sample_rate)
            self.assertAlmostEqual(wav.getnframes() / sample_rate, 0.8 / 1.03, delta=0.06)
        self.assertEqual(mode, "jarvis_reference")

    def test_legacy_jarvis_mode_maps_to_reference_preset(self) -> None:
        _wav_bytes, mode, _sample_rate = self.service.synthesize(
            SynthesisRequest(text="Зворотна сумісність.", mode="jarvis")
        )
        self.assertEqual(mode, "jarvis_reference")

    def test_stereo_resampling_preserves_duration(self) -> None:
        wav_bytes, _mode, sample_rate = self.service.synthesize(
            SynthesisRequest(
                text="Перевірка частоти дискретизації.",
                mode="jarvis_reference",
                sample_rate=44_100,
                speed=1.0,
            )
        )
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            self.assertEqual(sample_rate, 44_100)
            self.assertEqual(wav.getframerate(), 44_100)
            self.assertAlmostEqual(wav.getnframes() / sample_rate, 0.8, delta=0.06)

    def test_fx_override_can_force_raw(self) -> None:
        wav_bytes, mode, _sample_rate = self.service.synthesize(
            SynthesisRequest(text="Ефекти вимкнено.", mode="jarvis_reference", fx_enabled=False)
        )
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
        self.assertEqual(mode, "raw")


if __name__ == "__main__":
    unittest.main()
