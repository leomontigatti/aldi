"""The reading pipeline: a Source Document's page turned into Spanish blocks."""

import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from pydantic import ValidationError

from aldi.blocks import TRANSLATED, Block, Kind, captions_under_figures, has_emphasis
from aldi.source import SourcePage, page_image
from aldi.translator import AnswerBlock, PageBatchAnswer, Translator
from aldi.writer import TranslatedPage

PAGE_BATCH = 4
"""How many pages of a Scanned PDF Gemini reads in one request."""

# A box narrower than this once it is clipped to the page holds nothing to crop: Gemini placed it wrong.
_MIN_SIDE = 5
# An escape, or an emphasis mark, in a block's inline Markdown.
_MARK = re.compile(r"\\(.)|\*")
# A literal `*` on either side of some text with no other between them, as Gemini writes the emphasis it adds.
_STAR_PAIR = re.compile(r"(?<!\\)\\\*(?=\S)((?:(?!\\\*).)+?)(?<=\S)\\\*")


class UnusableAnswer(ValueError):
    """Gemini's answer for a Page Batch does not fit the pages it was sent."""


def translate_page(
    page: SourcePage, translator: Translator, terms: dict[str, str]
) -> tuple[TranslatedPage, dict[str, str]]:
    """The page with its text in Spanish, and the Term List after it. References and Figures stay as they are."""
    texts = [text for block in page.blocks if block.kind in TRANSLATED for text in _texts(block) if text]
    if not texts:
        return TranslatedPage(page.number, page.blocks), terms
    answer = translator.translate_texts(texts, terms)
    queue = iter(zip(texts, answer.texts, strict=True))

    def translated(text: str) -> str:
        if not text:
            return text
        original, result = next(queue)
        return _marked_as(original, result)

    blocks = []
    for block in page.blocks:
        if block.kind == Kind.TABLE:
            block = replace(block, rows=[[translated(cell) for cell in row] for row in block.rows])
        elif block.kind in TRANSLATED:
            block = replace(block, text=translated(block.text))
        blocks.append(block)
    return TranslatedPage(page.number, blocks), answer.terms


def _marked_as(original: str, spanish: str) -> str:
    """The translator's Spanish with only the marks the original has. Emphasis comes from the original's fonts, so
    a translator that adds some is not believed, nor the literal `*`s beyond the original's: Gemini writes its own
    emphasis escaped too, `\\*so\\*`. An escape of any other character, such as `\\[`, is that character."""
    emphasis = has_emphasis(original)
    if _stars(spanish) > _stars(original):
        spanish = _STAR_PAIR.sub(r"\1", spanish)
    stars = _stars(spanish) <= _stars(original)

    def mark(match: re.Match) -> str:
        escaped = match.group(1)
        if escaped is None:
            return "*" if emphasis else ""
        if escaped == "*":
            return match.group(0) if stars else ""
        return match.group(0) if escaped == "\\" else escaped

    return _MARK.sub(mark, spanish)


def _stars(text: str) -> int:
    """How many literal `*` the inline Markdown holds."""
    return sum(match.group(1) == "*" for match in _MARK.finditer(text))


def _texts(block: Block) -> list[str]:
    return [cell for row in block.rows for cell in row] if block.kind == Kind.TABLE else [block.text]


def read_page_batch(
    pages: Sequence[SourcePage], pdf: Path, folder: Path, translator: Translator, terms: dict[str, str]
) -> tuple[list[TranslatedPage], dict[str, str]]:
    """Scanned pages read and translated in one request, and the Term List after them.

    Their Figures and references are cropped from the page images of the PDF at `pdf` into `folder / "figures"`.
    Raises `UnusableAnswer` when the answer is not the JSON asked for, does not hold exactly the pages sent in their
    order, or puts a Figure or a reference off its page. None of such an answer is kept: when a page is missing
    from it, its text may sit in another page's.
    """
    numbers = [page.number for page in pages]
    reply = translator.read_page_batch([(number, page_image(pdf, number)) for number in numbers], terms)
    try:
        return _read_reply(reply.text, numbers, pdf, folder, terms)
    except UnusableAnswer as error:
        # Its Error Report names the model whose answer it was.
        error.gemini_model = reply.model
        raise


def _read_reply(
    text: str, numbers: list[int], pdf: Path, folder: Path, terms: dict[str, str]
) -> tuple[list[TranslatedPage], dict[str, str]]:
    try:
        answer = PageBatchAnswer.model_validate_json(text)
    except ValidationError as error:
        raise UnusableAnswer(f"not a Page Batch answer: {text!r:.500}") from error
    answered = [page.page for page in answer.pages]
    if answered != numbers:
        raise UnusableAnswer(f"sent pages {numbers}, the answer has pages {answered}")
    translated = [TranslatedPage(page.page, _page_blocks(page.page, page.blocks, pdf, folder)) for page in answer.pages]
    return translated, {**terms, **{term.original: term.spanish for term in answer.terms}}


def _page_blocks(number: int, answers: list[AnswerBlock], pdf: Path, folder: Path) -> list[Block]:
    blocks = []
    for answer in answers:
        if answer.kind in (Kind.FIGURE, Kind.REFERENCE):
            box = _box(answer.box_2d)
            if not box:
                raise UnusableAnswer(f"page {number}: a {answer.kind} at {answer.box_2d}, off the page")
            image = Path("figures") / f"{number:04d}-{len(blocks) + 1}.png"
            (folder / image).parent.mkdir(exist_ok=True)
            (folder / image).write_bytes(page_image(pdf, number, box))
            blocks.append(Block(answer.kind, image=image.as_posix(), box=box))
        elif answer.kind == Kind.TABLE:
            rows = [row for row in answer.rows if any(cell.strip() for cell in row)]
            if rows:
                blocks.append(Block(Kind.TABLE, rows=rows))
        elif answer.text.strip():
            blocks.append(Block(answer.kind, text=answer.text.strip()))
    return captions_under_figures(blocks)


def _box(box_2d: list[int]) -> tuple[int, int, int, int] | None:
    """Gemini's `ymin, xmin, ymax, xmax` as a Figure's box, clipped to the page; None when nothing is left of it."""
    if len(box_2d) != 4:
        return None
    ymin, xmin, ymax, xmax = (min(1000, max(0, value)) for value in box_2d)
    (x0, x1), (y0, y1) = sorted((xmin, xmax)), sorted((ymin, ymax))
    if x1 - x0 < _MIN_SIDE or y1 - y0 < _MIN_SIDE:
        return None
    return x0, y0, x1, y1
