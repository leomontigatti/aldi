"""Source inspection: what a Source Document's pages hold."""

from dataclasses import dataclass
from pathlib import Path

import pymupdf


class NotAPdfError(ValueError):
    pass


@dataclass(frozen=True)
class SourcePage:
    number: int
    """The page's number in the original, from 1."""
    texts: list[str]
    """The page's text blocks in reading order, each one paragraph with its line breaks joined."""


def read_pages(path: Path) -> list[SourcePage]:
    try:
        document = pymupdf.open(path, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise NotAPdfError(str(error)) from error
    with document:
        if not document.is_pdf:
            raise NotAPdfError(path.name)
        return [SourcePage(page.number + 1, _text_blocks(page)) for page in document]


def _text_blocks(page: pymupdf.Page) -> list[str]:
    texts = []
    for *_box, text, _number, kind in page.get_text("blocks", sort=True):
        joined = " ".join(text.split())
        if kind == 0 and joined:
            texts.append(joined)
    return texts
