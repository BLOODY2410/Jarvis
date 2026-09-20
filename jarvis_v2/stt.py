from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SttPolicy:
    primary_model: str = "whisper-large-v3-turbo"
    fallback_model: str = "whisper-large-v3"
    fast_max_speech_ms: int = 3500
    languages: tuple[str, ...] = ("uk", "ru")
    custom_vocabulary: tuple[str, ...] = ()

    def models_for(self, speech_ms: int) -> tuple[str, ...]:
        if speech_ms <= self.fast_max_speech_ms and self.primary_model != self.fallback_model:
            return self.primary_model, self.fallback_model
        return (self.fallback_model,)

    def prompt(self) -> str:
        vocabulary = ", ".join(self.custom_vocabulary)
        prompt = (
            "Українська, російська або суржикова команда для персонального асистента Джарвіс. "
            "Зберігай мову мовця й правильно пиши власні назви."
        )
        if vocabulary:
            prompt += f" Словник: {vocabulary}."
        # Groq Whisper has a 224-token prompt limit; this conservative bound
        # leaves room for multilingual tokenization.
        return prompt[:800]
