"""Source inspection: what a Source Document's pages hold, as blocks in reading order."""

import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

import pymupdf

from aldi.blocks import Block, Kind, plain


class NotAPdfError(ValueError):
    pass


@dataclass(frozen=True)
class SourcePage:
    number: int
    """The page's number in the original, from 1."""
    blocks: list[Block]
    """The page's content in reading order, in the original language."""


# Ligatures (`ﬁ`) come out as the letters they join, which is what a translator and a search expect.
_TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_IMAGES | pymupdf.TEXT_MEDIABOX_CLIP
# Running headers, footers and page numbers sit in the top or bottom 8% of a page.
_MARGIN = 0.08
_HEADING_SIZE = 1.15
_CAPTION = re.compile(r"(fig(ure|ura)?s?\.?)\s*\d", re.IGNORECASE)
_REFERENCES_TITLE = re.compile(
    r"(\d+\.?\s*)?(references|referências|referências bibliográficas|referencias|referencias bibliográficas|"
    r"bibliography|bibliografia|bibliografía|literature cited|works cited)\s*:?",
    re.IGNORECASE,
)
_PAGE_NUMBER = re.compile(r"[\divxlc]+", re.IGNORECASE)
_TYPST_IMAGES = {"png", "jpeg", "jpg", "gif"}


def read_pages(path: Path, folder: Path) -> list[SourcePage]:
    """The document's pages; their Figures' images are written to `folder / "figures"`."""
    try:
        document = pymupdf.open(path, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise NotAPdfError(str(error)) from error
    with document:
        if not document.is_pdf:
            raise NotAPdfError(path.name)
        pages = [_read_items(page, folder) for page in document]
    body_size = _body_size(pages)
    running = _running_texts(pages)
    in_references = False
    result = []
    for number, items in enumerate(pages, start=1):
        blocks = []
        for item in items:
            block = _classify(item, body_size, running)
            if block.kind == Kind.HEADING:
                in_references = bool(_REFERENCES_TITLE.fullmatch(plain(block.text)))
            elif block.kind == Kind.PARAGRAPH and in_references:
                block = replace(block, kind=Kind.REFERENCE)
            blocks.append(block)
        result.append(SourcePage(number, _captions_under_figures(blocks)))
    return result


@dataclass
class _Item:
    rect: pymupdf.Rect
    page: pymupdf.Rect
    block: Block
    size: float = 0
    """The text's dominant font size, 0 for a Figure or a table."""
    sizes: Counter = field(default_factory=Counter)


def _read_items(page: pymupdf.Page, folder: Path) -> list[_Item]:
    items = []
    tables = [table for table in page.find_tables().tables if table.row_count]
    for table in tables:
        rows = [[_escape(" ".join((cell or "").split())) for cell in row] for row in table.extract()]
        items.append(_Item(pymupdf.Rect(table.bbox), page.rect, Block(Kind.TABLE, rows=rows)))
    blocks = page.get_text("dict", flags=_TEXT_FLAGS)["blocks"]
    # Text printed over a Figure (its labels and arrows) is part of it, and a table's text is in its cells.
    taken = [pymupdf.Rect(table.bbox) for table in tables]
    taken += [pymupdf.Rect(block["bbox"]) for block in blocks if block["type"] == 1]
    figures = 0
    for block in blocks:
        rect = pymupdf.Rect(block["bbox"])
        if block["type"] == 1:
            figures += 1
            image = _save_image(block, folder / "figures" / f"{page.number + 1:04d}-{figures}")
            items.append(_Item(rect, page.rect, Block(Kind.FIGURE, image=image, box=_normalized(rect, page.rect))))
            continue
        if any(other.contains((rect.tl + rect.br) / 2) for other in taken):
            continue
        spans = [span for line in block["lines"] for span in line["spans"]]
        text = _inline(block["lines"])
        if text:
            sizes = Counter()
            for span in spans:
                sizes[round(span["size"] * 2) / 2] += len(span["text"].strip())
            item = _Item(rect, page.rect, Block(Kind.PARAGRAPH, text=text), sizes.most_common(1)[0][0], sizes)
            items.append(item)
    return _reading_order(items, page.rect.width)


def _save_image(block: dict, stem: Path) -> str:
    """Writes a Figure's image as a file Typst and Pandoc read; returns its path relative to the Job folder."""
    extension, data = block["ext"].lower(), block["image"]
    if extension not in _TYPST_IMAGES:
        pixmap = pymupdf.Pixmap(data)
        if pixmap.colorspace and pixmap.colorspace.n > 3:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
        extension, data = "png", pixmap.tobytes("png")
    stem.parent.mkdir(parents=True, exist_ok=True)
    path = stem.with_suffix(f".{extension}")
    path.write_bytes(data)
    return path.relative_to(stem.parent.parent).as_posix()


def _normalized(rect: pymupdf.Rect, page: pymupdf.Rect) -> tuple[int, int, int, int]:
    def scale(value: float, length: float) -> int:
        return min(1000, max(0, round(value / length * 1000)))

    return (
        scale(rect.x0, page.width),
        scale(rect.y0, page.height),
        scale(rect.x1, page.width),
        scale(rect.y1, page.height),
    )


def _inline(lines: list[dict]) -> str:
    """A text block's spans as one inline-Markdown paragraph, its line breaks joined and its emphasis marked."""
    runs: list[list] = []
    for line in lines:
        for span in [*line["spans"], {"text": " ", "flags": 0, "font": ""}]:
            text = re.sub(r"\s+", " ", span["text"])
            font = span["font"].lower()
            bold = bool(span["flags"] & pymupdf.TEXT_FONT_BOLD) or "bold" in font
            italic = bool(span["flags"] & pymupdf.TEXT_FONT_ITALIC) or "italic" in font or "oblique" in font
            if not text.strip():
                bold = italic = False
            if runs and runs[-1][1:] == [bold, italic]:
                runs[-1][0] += text
            elif text:
                runs.append([text, bold, italic])
    parts = []
    for text, bold, italic in runs:
        escaped = _escape(text)
        marker = "*" * (2 * bold + italic)
        core = escaped.strip()
        if marker and core:
            escaped = escaped.replace(core, f"{marker}{core}{marker}", 1)
        parts.append(escaped)
    return " ".join("".join(parts).split())


def _escape(text: str) -> str:
    return re.sub(r"([\\*])", r"\\\1", text)


def _reading_order(items: list[_Item], width: float) -> list[_Item]:
    """Top to bottom; where the page has two columns, the left one before the right one."""
    middle = width / 2

    def column(item: _Item) -> int | None:
        if item.rect.x1 <= middle + 10:
            return 0
        if item.rect.x0 >= middle - 10:
            return 1
        return None

    ordered, section = [], []
    for item in sorted(items, key=lambda item: (item.rect.y0, item.rect.x0)):
        if column(item) is None:
            ordered += sorted(section, key=lambda item: (column(item), item.rect.y0))
            ordered.append(item)
            section = []
        else:
            section.append(item)
    return ordered + sorted(section, key=lambda item: (column(item), item.rect.y0))


def _body_size(pages: list[list[_Item]]) -> float:
    sizes = Counter()
    for items in pages:
        for item in items:
            sizes.update(item.sizes)
    return sizes.most_common(1)[0][0] if sizes else 0


def _in_margin(item: _Item) -> bool:
    return item.rect.y1 <= item.page.height * _MARGIN or item.rect.y0 >= item.page.height * (1 - _MARGIN)


def _running_key(text: str) -> str:
    return " ".join(re.sub(r"\d+", " ", plain(text)).split()).lower()


def _running_texts(pages: list[list[_Item]]) -> set[str]:
    """The margin texts that repeat on more than one page once their numbers are left out."""
    seen = Counter()
    for items in pages:
        seen.update({_running_key(item.block.text) for item in items if item.size and _in_margin(item)})
    return {key for key, count in seen.items() if count > 1}


def _classify(item: _Item, body_size: float, running: set[str]) -> Block:
    block = item.block
    if block.kind != Kind.PARAGRAPH:
        return block
    text = plain(block.text)
    if _in_margin(item) and (_PAGE_NUMBER.fullmatch(text) or _running_key(block.text) in running):
        return replace(block, kind=Kind.FURNITURE)
    if _REFERENCES_TITLE.fullmatch(text) or (item.size >= body_size * _HEADING_SIZE and len(text) <= 200):
        return replace(block, kind=Kind.HEADING)
    if _CAPTION.match(text):
        return replace(block, kind=Kind.CAPTION)
    return block


def _captions_under_figures(blocks: list[Block]) -> list[Block]:
    """A Figure's caption printed above it moves below it, where the writer looks for it."""
    blocks = list(blocks)
    for index in range(len(blocks) - 1):
        if blocks[index].kind == Kind.CAPTION and blocks[index + 1].kind == Kind.FIGURE:
            blocks[index], blocks[index + 1] = blocks[index + 1], blocks[index]
    return blocks
