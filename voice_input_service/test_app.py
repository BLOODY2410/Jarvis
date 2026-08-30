import io
import os
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from app import (
    FRAME_MS,
    FRAME_SAMPLES,
    SAMPLE_RATE,
    Settings,
    VoiceInputEngine,
    contains_wake_word,
    frames_for_ms,
    pcm_to_wav,
    read_pcm_wav,
)


def test_settings() -> Settings:
    return Settings(
        "key", "whisper-large-v3", "uk", None, 0.45, 2, 160, 20, 2,
        True, 200, 0.001, 0, "Українська команда",
    )


class VoiceInputTests(unittest.TestCase):
    def test_environment_defaults_match_stable_voice_profile(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "key"}, clear=True):
            settings = Settings.from_environment()
        self.assertEqual(settings.vad_aggressiveness, 1)
        self.assertEqual(settings.vad_energy_ratio, 1.10)
        self.assertEqual(settings.vad_energy_delta, 0.004)
        self.assertEqual(settings.vad_start_chunks, 1)
        self.assertEqual(settings.pre_roll_ms, 560)
        self.assertEqual(settings.post_roll_ms, 480)
        self.assertEqual(settings.silence_ms, 1200)
        self.assertEqual(settings.min_speech_ms, 300)
        self.assertEqual(settings.min_audio_rms, 0.0015)
        self.assertEqual(settings.post_tts_guard_ms, 250)
        self.assertFalse(settings.barge_in_enabled)
        self.assertEqual(settings.wake_vosk_max_edit_distance, 1)

    def test_pcm_to_wav_contract(self):
        payload = pcm_to_wav(np.zeros(FRAME_SAMPLES, dtype=np.int16).tobytes())
        with wave.open(io.BytesIO(payload), "rb") as wav:
            self.assertEqual(wav.getframerate(), SAMPLE_RATE)
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)

    def test_diagnostic_saves_raw_and_actual_whisper_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(test_settings(), diagnostic=True, diagnostic_dir=Path(directory))
            engine = VoiceInputEngine(settings)
            raw_pcm = np.zeros(FRAME_SAMPLES * 4, dtype=np.int16).tobytes()
            whisper_pcm = np.zeros(FRAME_SAMPLES * 2, dtype=np.int16).tobytes()
            engine._save_diagnostic_wavs(raw_pcm, whisper_pcm)
            self.assertEqual(read_pcm_wav(Path(directory) / "last_raw.wav"), raw_pcm)
            self.assertEqual(read_pcm_wav(Path(directory) / "last_whisper.wav"), whisper_pcm)

    def test_wake_event_switches_to_active_mode(self):
        engine = VoiceInputEngine(test_settings())
        engine._wake_model = Mock()
        engine._wake_model.models = {"hey_jarvis": object()}
        engine._wake_model.predict.return_value = {"hey_jarvis": 0.9}
        engine.process_frame(np.zeros(FRAME_SAMPLES, dtype=np.int16).tobytes())
        self.assertEqual(engine.events.get_nowait(), {"type": "wake"})
        self.assertTrue(engine.get_state()["conversation_active"])

    def test_wake_wav_reports_measured_score(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / "wake.wav"
            wav_path.write_bytes(pcm_to_wav(np.zeros(FRAME_SAMPLES * 2, dtype=np.int16).tobytes()))
            engine = VoiceInputEngine(test_settings())
            engine._wake_model = Mock()
            engine._wake_model.predict.side_effect = [
                {engine._wake_model_name: 0.12},
                {engine._wake_model_name: 0.61},
            ]
            result = engine.analyze_wake_wav(wav_path)
            self.assertTrue(result["wake_detected"])
            self.assertEqual(result["detected_at_ms"], FRAME_MS)
            self.assertEqual(result["max_openwakeword_score"], 0.61)

    def test_ukrainian_wake_variants(self):
        self.assertTrue(contains_wake_word("джарвіс"))
        self.assertTrue(contains_wake_word("хей джарвис"))
        self.assertTrue(contains_wake_word("жарвіс"))
        self.assertTrue(contains_wake_word("Джарвіс,"))
        self.assertFalse(contains_wake_word("жарвіс", max_edit_distance=0))
        self.assertFalse(contains_wake_word("джеремі"))
        self.assertFalse(contains_wake_word("джарти"))
        self.assertFalse(contains_wake_word("відкрий браузер"))
        self.assertFalse(contains_wake_word("джерело"))
        self.assertTrue(contains_wake_word("джеремі", ("джеремі",)))

    def test_barge_in_emits_interrupt_then_starts_stt(self):
        engine = VoiceInputEngine(test_settings())
        engine.set_state(True, True)
        speech = np.full(FRAME_SAMPLES, 12000, dtype=np.int16).tobytes()
        silence = np.zeros(FRAME_SAMPLES, dtype=np.int16).tobytes()
        with patch.object(engine, "_is_speech", side_effect=[True, True, False, False]), \
             patch.object(engine, "_transcribe") as transcribe:
            for frame in [speech, speech, silence, silence]:
                engine.process_frame(frame)
            self.assertEqual(engine.events.get_nowait(), {"type": "interrupt"})
            for _ in range(20):
                if transcribe.called:
                    break
                __import__("time").sleep(0.01)
            self.assertTrue(transcribe.called)

    def test_vad_keeps_pre_roll_and_only_configured_post_roll(self):
        settings = replace(test_settings(), pre_roll_ms=160, post_roll_ms=80)
        engine = VoiceInputEngine(settings)
        engine.set_state(True, False)
        speech = np.full(FRAME_SAMPLES, 12000, dtype=np.int16).tobytes()
        silence = np.zeros(FRAME_SAMPLES, dtype=np.int16).tobytes()
        with patch.object(engine, "_is_speech", side_effect=[False, False, True, True, False, False]), \
             patch.object(engine, "_transcribe") as transcribe:
            for frame in [silence, silence, speech, speech, silence, silence]:
                engine.process_frame(frame)
            for _ in range(20):
                if transcribe.called:
                    break
                __import__("time").sleep(0.01)
            raw_frames, whisper_frames, speech_ms = transcribe.call_args.args
            self.assertEqual(len(raw_frames), 6)
            self.assertEqual(len(whisper_frames), 5)
            self.assertEqual(speech_ms, 2 * FRAME_MS)

    def test_roll_intervals_round_up_instead_of_shortening(self):
        self.assertEqual(frames_for_ms(1), 1)
        self.assertEqual(frames_for_ms(80), 1)
        self.assertEqual(frames_for_ms(81), 2)

    def test_active_session_updates_noise_floor_below_gate_even_if_webrtc_says_speech(self):
        engine = VoiceInputEngine(test_settings())
        engine._noise_history.extend([0.01] * 10)
        engine.set_state(True, False)
        quiet = np.full(FRAME_SAMPLES, 328, dtype=np.int16).tobytes()
        before = len(engine._noise_history)
        with patch.object(engine, "_is_speech", return_value=True):
            engine.process_frame(quiet)
        self.assertEqual(len(engine._noise_history), before + 1)
        self.assertFalse(engine._last_energy_gate)

    def test_stt_busy_explicitly_discards_partial_capture_and_pre_roll(self):
        engine = VoiceInputEngine(test_settings())
        engine.set_state(True, False)
        frame = np.zeros(FRAME_SAMPLES, dtype=np.int16).tobytes()
        engine._capture.append(frame)
        engine._pre_roll.append(frame)
        engine._speech_seen = True
        engine._stt_busy = True
        with patch.object(engine, "_is_speech") as is_speech:
            engine.process_frame(frame)
        self.assertFalse(is_speech.called)
        self.assertFalse(engine._capture)
        self.assertFalse(engine._pre_roll)
        self.assertFalse(engine._speech_seen)

    def test_adaptive_energy_gate_rejects_stationary_noise_even_when_webrtc_votes_speech(self):
        engine = VoiceInputEngine(test_settings())
        engine._noise_history.extend([0.10] * 100)
        engine.set_state(True, False)
        stationary_noise = np.full(FRAME_SAMPLES, 3277, dtype=np.int16).tobytes()
        with patch.object(engine, "_is_speech", return_value=True), \
             patch.object(engine, "_transcribe") as transcribe:
            for _ in range(30):
                engine.process_frame(stationary_noise)
            self.assertFalse(engine._speech_seen)
            self.assertFalse(transcribe.called)
            self.assertTrue(engine._last_vad_raw)
            self.assertFalse(engine._last_energy_gate)


if __name__ == "__main__":
    unittest.main()
