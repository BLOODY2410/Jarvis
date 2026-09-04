from jarvis_v2.models import PersonalityMode
from jarvis_v2.persona import Persona, infer_personality
from jarvis_v2.text import voice_budget


def test_voice_budget_keeps_complete_sentences() -> None:
    answer = "Перше речення завершене. Друге теж завершене. Третє вже зайве."
    budgeted = voice_budget(answer, max_chars=45, max_sentences=2)
    assert budgeted == "Перше речення завершене. Друге теж завершене."
    assert budgeted.endswith(".")


def test_voice_budget_never_cuts_one_long_sentence() -> None:
    answer = "Це одне довге, але повністю завершене речення, яке не можна обрізати посеред слова."
    assert voice_budget(answer, max_chars=20, max_sentences=1) == answer


def test_persona_modes_and_sparse_sir() -> None:
    assert infer_personality("Ти сьогодні трохи тупиш") == PersonalityMode.BANTER
    assert infer_personality("Це серйозна помилка") == PersonalityMode.SERIOUS
    persona = Persona()
    assert "сер" in persona.finish("Готово, сер.", voice=True).lower()
    assert "сер" not in persona.finish("Саме так, сер.", voice=True).lower()
