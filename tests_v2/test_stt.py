import pytest

from jarvis_v2.config import Settings
from jarvis_v2.stt import SttPolicy
from jarvis_v2.voice import InProcessVoiceRuntime


def test_stt_primary_and_fallback_policy() -> None:
    policy = SttPolicy(fast_max_speech_ms=3500)
    assert policy.models_for(1200) == ("whisper-large-v3-turbo", "whisper-large-v3")
    assert policy.models_for(5000) == ("whisper-large-v3",)


def test_stt_prompt_supports_surzhyk_and_custom_vocabulary() -> None:
    prompt = SttPolicy(custom_vocabulary=("YouTube Music", "BLOODY2410")).prompt()
    assert "Українська, російська або суржикова" in prompt
    assert "YouTube Music" in prompt
    assert "BLOODY2410" in prompt


@pytest.mark.asyncio
async def test_desktop_activation_request_stays_local(tmp_path) -> None:
    runtime = InProcessVoiceRuntime(Settings.from_env(tmp_path))
    request = tmp_path / ".run" / "activate-request"
    request.parent.mkdir(parents=True)
    request.write_text("listen", encoding="ascii")
    event = await runtime.next_event(timeout=0.01)
    assert event.kind == "wake"
    assert not request.exists()
