from __future__ import annotations

from dataclasses import dataclass

from jarvis_v2.models import PersonalityMode
from jarvis_v2.text import voice_budget

BASE_PERSONA = """Ти JARVIS — точний, стриманий і дотепний персональний асистент.
Користувач говорить українською, російською, суржиком, зі сленгом і помилками STT.
Розумій значення за контекстом, не виправляй користувача й відповідай природною українською.
Не копіюй карикатурний суржик. Не вигадуй виконані дії, дані з екрана або актуальні факти.
Слово «сер» використовуй зрідка: не у двох коротких відповідях поспіль і не частіше приблизно чверті відповідей.
Для голосу дай спершу коротку самодостатню відповідь повними реченнями."""

MODE_GUIDANCE = {
    PersonalityMode.ACTION: "Підтверджуй лише фактично успішний результат інструмента, одним реченням.",
    PersonalityMode.PC_AGENT: "Будь операційно точним. Помилку інструмента називай прямо, без удаваного успіху.",
    PersonalityMode.LIVE_INFO: "Кожне актуальне твердження має спиратися на повернуті джерела.",
    PersonalityMode.SERIOUS: "Без жартів. Дай точну, спокійну відповідь.",
    PersonalityMode.BANTER: "Дозволений один сухий дотеп, без образ і без багатослів'я.",
    PersonalityMode.CASUAL: "Відповідай тепло й природно, але стисло.",
}


@dataclass(slots=True)
class Persona:
    max_chars: int = 280
    max_sentences: int = 2
    replies: int = 0
    last_used_sir: bool = False

    def prompt(self, mode: PersonalityMode) -> str:
        return f"{BASE_PERSONA}\nРежим: {mode.value}. {MODE_GUIDANCE[mode]}"

    def finish(self, text: str, *, voice: bool) -> str:
        self.replies += 1
        cleaned = " ".join(text.split()).strip()
        if self.last_used_sir and "сер" in cleaned.lower():
            cleaned = cleaned.replace(", сер", "").replace(" сер.", ".").replace("Сер, ", "")
        self.last_used_sir = "сер" in cleaned.lower()
        return voice_budget(cleaned, self.max_chars, self.max_sentences) if voice else cleaned


def infer_personality(text: str, action: bool = False, live: bool = False) -> PersonalityMode:
    lowered = text.lower()
    if live:
        return PersonalityMode.LIVE_INFO
    if action:
        return PersonalityMode.ACTION
    if any(marker in lowered for marker in ("туп", "бездар", "груст", "сумн", "тренуватись")):
        return PersonalityMode.BANTER
    if any(marker in lowered for marker in ("помилка", "не працює", "важливо", "серйозно", "температур")):
        return PersonalityMode.SERIOUS
    return PersonalityMode.CASUAL
