from __future__ import annotations

import io
import unittest
import wave
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from app import FxSettings, JarvisFx, PiperVoiceService, SynthesisRequest, VoiceSettings


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
