from jarvis_v2.intents import deterministic_intent
from jarvis_v2.models import (
    AdjustVolumeIntent,
    OpenAppIntent,
    OpenUrlIntent,
    Route,
    SetVolumeIntent,
    WindowsSettingsIntent,
)


def first(text: str):
    envelope = deterministic_intent(text)
    assert envelope is not None
    assert envelope.intents
    return envelope, envelope.intents[0]


def test_surzhyk_volume_intent() -> None:
    envelope, intent = first("Джарвіс, поставь мені звук на п'ятдесят, будь ласка")
    assert envelope.route == Route.PC_AGENT
    assert intent == SetVolumeIntent(level=50)


def test_volume_variants() -> None:
    cases = {
        "постав гучність на 35%": SetVolumeIntent(level=35),
        "зроби звук на сотку": SetVolumeIntent(level=100),
        "зроби гучність на половину": SetVolumeIntent(level=50),
        "чуть потише": AdjustVolumeIntent(delta=-10),
        "добавь звук": AdjustVolumeIntent(delta=10),
    }
    for phrase, expected in cases.items():
        _, intent = first(phrase)
        assert intent == expected


def test_windows_settings_routes() -> None:
    _, intent = first("відкрий налаштування звуку")
    assert intent == WindowsSettingsIntent(page="sound")
    _, intent = first("відкрий windows update")
    assert intent == WindowsSettingsIntent(page="windows_update")


def test_open_news_is_live_not_open_app() -> None:
    envelope = deterministic_intent("Відкрий новини")
    assert envelope is not None
    assert envelope.route == Route.LIVE_CURRENT
    assert not any(isinstance(intent, OpenAppIntent) for intent in envelope.intents)


def test_known_site_uses_url_not_app() -> None:
    _, intent = first("Відкрий YouTube")
    assert isinstance(intent, OpenUrlIntent)
    assert intent.url == "https://www.youtube.com"


def test_malformed_transliterated_command_is_safe() -> None:
    envelope = deterministic_intent("Við grey YouTube")
    assert envelope is not None
    assert envelope.needs_clarification
    assert envelope.confidence == 0
    assert not any(isinstance(intent, (OpenAppIntent, OpenUrlIntent)) for intent in envelope.intents)


def test_multi_intent_keeps_order() -> None:
    envelope = deterministic_intent("відкрий Chrome а потім постав гучність на 30")
    assert envelope is not None
    assert [intent.kind for intent in envelope.intents] == ["open_app", "set_volume"]
    assert envelope.intents[0] == OpenAppIntent(app="chrome")
    assert envelope.intents[1] == SetVolumeIntent(level=30)
