from __future__ import annotations

import io
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from app import (
    ACKNOWLEDGEMENTS,
    AudioCache,
    FishAudioError,
    FishAudioSettings,
    FishAudioService,
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
            speed=0.97,
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

    def test_paid_fish_fallback_is_forbidden_by_default(self) -> None:
        fish = FishAudioService(self.fish_settings, JarvisFx(FxSettings.from_environment()))
        with patch.object(fish, "_request_audio", side_effect=FishAudioError("rejected", 400)) as request:
            with self.assertRaises(FishAudioError):
                fish.synthesize(SynthesisRequest(text="Без платного fallback."))
        self.assertEqual(request.call_count, 1)

    def test_paid_fish_fallback_requires_explicit_opt_in(self) -> None:
        settings = replace(self.fish_settings, allow_paid_fallback=True)
        fish = FishAudioService(settings, JarvisFx(FxSettings.from_environment()))
        audio = encode_wav(np.zeros(2_000, dtype=np.float32), 44_100)
        with patch.object(fish, "_request_audio", side_effect=[FishAudioError("rejected", 400), audio]) as request:
            result = fish.synthesize(SynthesisRequest(text="Явний opt-in."))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result.model, "s2-pro")

    def test_fish_cinematic_speed_is_used_by_streaming_and_wav_payloads(self) -> None:
        fish = FishAudioService(self.fish_settings, JarvisFx(FxSettings.from_environment()))
        request = SynthesisRequest(text="Перевірка швидкості.")
        self.assertEqual(fish._payload(request, streaming=False)["prosody"]["speed"], 0.97)
        self.assertEqual(fish._payload(request, streaming=True)["prosody"]["speed"], 0.97)
        override = SynthesisRequest(text="Перевірка швидкості.", speed=1.02)
        self.assertEqual(fish._payload(override, streaming=True)["prosody"]["speed"], 1.02)

    def test_ack_pool_is_small_and_uses_cinematic_outcomes(self) -> None:
        legacy: str = "".join(("п", "а", "н", "е"))
        self.assertGreaterEqual(len(ACKNOWLEDGEMENTS), 4)
        self.assertLessEqual(len(ACKNOWLEDGEMENTS), 6)
        self.assertNotIn("working", ACKNOWLEDGEMENTS)
        for phrase in ACKNOWLEDGEMENTS.values():
            self.assertNotIn(legacy, phrase.casefold())
            self.assertLessEqual(len(phrase.split()), 10)

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
