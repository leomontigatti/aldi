"""The seam to Gemini: what the rest of the app asks of a translator, and nothing about how it is done."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from aldi.blocks import Kind


@dataclass(frozen=True)
class TextTranslation:
    """The Spanish of each text, in the order sent, and the Term List as it stands after them."""

    texts: list[str]
    terms: dict[str, str] = field(default_factory=dict)


class Term(BaseModel):
    original: str
    spanish: str


class AnswerBlock(BaseModel):
    kind: Kind
    text: str = ""
    """The Spanish, inline Markdown like `aldi.blocks.Block.text`; empty for a Figure, a reference or furniture."""
    rows: list[list[str]] = []
    """A table's cells in Spanish, row by row, the first row its header."""
    box_2d: list[int] = []
    """Where a Figure or a reference is on its page: ymin, xmin, ymax, xmax, normalized 0 to 1000."""


class AnswerPage(BaseModel):
    page: int
    """The page's number, as the request labelled its image."""
    blocks: list[AnswerBlock]
    """The page's content in reading order."""


class PageBatchAnswer(BaseModel):
    """What Gemini answers for a Page Batch: the Spanish of each page sent, and the terms those pages add."""

    pages: list[AnswerPage]
    terms: list[Term]


@dataclass(frozen=True)
class PageBatchReply:
    """Gemini's answer to a Page Batch as it gave it, and the model that gave it, which a report about it names."""

    text: str
    model: str = ""


class Translator(Protocol):
    def translate_texts(self, texts: Sequence[str], terms: Mapping[str, str]) -> TextTranslation:
        """Translate text blocks taken from a Born-digital PDF's text layer into Spanish.

        Each text is inline Markdown (`aldi.blocks.Block.text`); its Spanish keeps the same emphasis and escapes.
        """
        ...

    def read_page_batch(self, pages: Sequence[tuple[int, bytes]], terms: Mapping[str, str]) -> PageBatchReply:
        """Read a Page Batch of a Scanned PDF, each page its number and its PNG image, and translate it into Spanish.

        Returns the answer as Gemini gave it, JSON meant to match `PageBatchAnswer`: the reading pipeline checks it,
        since nothing in it can be taken on trust.
        """
        ...

    def answer_layout_prompt(self, prompt: str) -> str:
        """Answer a prompt `pdf2zh-next` wrote for a Layout-kept Translation, with the text Gemini gives.

        The prompt holds its own instructions and the shape of the answer it expects; `pdf2zh-next` checks it.
        """
        ...


class QuotaExhausted(Exception):
    """No model in the Model Chain takes a request now: each is out of quota, or stays busy. A Quota Pause."""

    def __init__(self, retry_after: float | None = None):
        super().__init__()
        self.retry_after = retry_after
        """The seconds to wait before asking again, when Gemini says; the Quota Pause's own otherwise."""


class Offline(Exception):
    """Gemini cannot be reached: no internet connection."""


class KeyRefused(Exception):
    """Gemini refuses the key, as when she deleted it in AI Studio: nothing works until she saves a new one."""
