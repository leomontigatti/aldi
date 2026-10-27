"""The seam to Gemini: what the rest of the app asks of a translator, and nothing about how it is done."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class TextTranslation:
    """The Spanish of each text, in the order sent, and the Term List as it stands after them."""

    texts: list[str]
    terms: dict[str, str] = field(default_factory=dict)


class Translator(Protocol):
    def translate_texts(self, texts: Sequence[str], terms: Mapping[str, str]) -> TextTranslation:
        """Translate text blocks taken from a Born-digital PDF's text layer into Spanish."""
        ...


class QuotaExhausted(Exception):
    """No model in the Model Chain takes a request now: each is out of quota, or stays busy. A Quota Pause."""


class Offline(Exception):
    """Gemini cannot be reached: no internet connection."""
