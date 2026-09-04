from jarvis_v2.stt import SttPolicy


def test_stt_primary_and_fallback_policy() -> None:
    policy = SttPolicy(fast_max_speech_ms=3500)
    assert policy.models_for(1200) == ("whisper-large-v3-turbo", "whisper-large-v3")
    assert policy.models_for(5000) == ("whisper-large-v3",)


def test_stt_prompt_supports_surzhyk_and_custom_vocabulary() -> None:
    prompt = SttPolicy(custom_vocabulary=("YouTube Music", "BLOODY2410")).prompt()
    assert "Українська, російська або суржикова" in prompt
    assert "YouTube Music" in prompt
    assert "BLOODY2410" in prompt
