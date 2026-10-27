"""Source inspection: what a Source Document's pages hold, as blocks in reading order."""

import re
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from pathlib import Path

import pymupdf

from aldi.blocks import Block, Kind, captions_under_figures, plain


class NotAPdfError(ValueError):
    pass


@dataclass(frozen=True)
class SourcePage:
    number: int
    """The page's number in the original, from 1."""
    blocks: list[Block]
    """The page's content in reading order, in the original language; none for a scanned page."""
    scanned: bool = False
    """The page is a photo with no text layer: Gemini reads its image."""


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
# A list item's printed marker: a bullet, or an enumerator such as `1.`, `1)`, `a)`, `(1)` or `ii.`.
_LIST_MARKER = re.compile(r"(?:[•◦▪]|\d{1,3}[.)]|[a-z]\)|\(\d{1,3}\)|[ivx]{1,4}\.)\s+")
_TYPST_IMAGES = {"png", "jpeg", "jpg", "gif"}
# A scan is rarely sharper than this: higher only makes the images Gemini reads and the crops larger.
_DPI = 150
# A drawing is a Figure when both its sides reach this many points (about 2 cm): rules and underlines do not.
_FIGURE_SIDE = 50
# A drawing that frames more text than this is a box around a passage, not a Figure with labels.
_FIGURE_TEXT = 200
# A text block of this many words ending like a sentence is prose, not a Figure's label.
_SENTENCE_WORDS = 4
# Rectangles this many or more, none framing the rest, are a drawing's shapes (a bar chart), not its frame.
_FIGURE_SHAPES = 3
# A path at least this share of its cluster's width and height frames it (a border, a shading).
_FRAME = 0.9
# A drawing this close to the page's size is its border.
_PAGE_BORDER = 0.95


def inspect(path: Path) -> list[bool]:
    """Whether each page of the PDF at `path` is scanned, one flag a page in order."""
    with _open(path) as document:
        return [_is_scanned(page) for page in document]


def read_pages(path: Path, folder: Path, numbers: Collection[int] | None = None) -> list[SourcePage]:
    """The document's pages, or those `numbers` names; their Figures' images are written to `folder / "figures"`."""
    with _open(path) as document:
        chosen = [page for page in document if numbers is None or page.number + 1 in numbers]
        scanned = {page.number + 1 for page in chosen if _is_scanned(page)}
        pages = [(page.number + 1, [] if page.number + 1 in scanned else _read_items(page, folder)) for page in chosen]
    body_size = _body_size([items for _number, items in pages])
    running = _running_texts([items for _number, items in pages])
    in_references = False
    result = []
    for number, items in pages:
        blocks = []
        for item in items:
            block = _classify(item, body_size, running)
            if block.kind == Kind.HEADING:
                in_references = bool(_REFERENCES_TITLE.fullmatch(plain(block.text)))
            elif block.kind == Kind.PARAGRAPH and in_references:
                block = replace(block, kind=Kind.REFERENCE)
            elif block.kind == Kind.PARAGRAPH and (marker := _LIST_MARKER.match(block.text)):
                # The writer prints its own bullet.
                block = replace(block, kind=Kind.LIST_ITEM, text=block.text[marker.end() :])
            blocks.append(block)
        result.append(SourcePage(number, captions_under_figures(blocks), scanned=number in scanned))
    return result


def _open(path: Path) -> pymupdf.Document:
    try:
        document = pymupdf.open(path, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise NotAPdfError(str(error)) from error
    if not document.is_pdf:
        document.close()
        raise NotAPdfError(path.name)
    return document


def _is_scanned(page: pymupdf.Page) -> bool:
    return not page.get_text().strip() and bool(page.get_images())


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
    drawn = _drawn_figures(page, blocks, taken)
    for number, rect in enumerate(drawn, start=1):
        image = _save_drawing(page, rect, folder / "figures" / f"{page.number + 1:04d}-d{number}.png")
        items.append(_Item(rect, page.rect, Block(Kind.FIGURE, image=image, box=_normalized(rect, page.rect))))
    taken += drawn
    figures = 0
    for block in blocks:
        rect = pymupdf.Rect(block["bbox"])
        if block["type"] == 1:
            if any(other.intersects(rect) for other in drawn):
                # The drawing around it took it in.
                continue
            taken.append(rect)
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


def _drawn_figures(page: pymupdf.Page, blocks: list[dict], tables: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """Where the page draws a Figure in vectors (a diagram or a chart): a cluster of drawings large enough, not a
    table, drawing more than a frame around its text, and holding no more text than a Figure's labels. A cluster
    over a bitmap takes the bitmap in: arrows and labels drawn on a photo are one Figure with it. Each box takes in
    its stroke's edge."""
    images = [pymupdf.Rect(block["bbox"]) for block in blocks if block["type"] == 1]
    drawings = page.get_drawings()
    figures: list[pymupdf.Rect] = []
    for cluster in page.cluster_drawings(drawings=drawings):
        rect = (cluster + (-2, -2, 2, 2)) & page.rect
        if any(rect.intersects(table) for table in tables):
            continue
        if rect.width >= page.rect.width * _PAGE_BORDER and rect.height >= page.rect.height * _PAGE_BORDER:
            continue
        over = [image for image in images if rect.intersects(image)]
        if not over and min(rect.width, rect.height) < _FIGURE_SIDE:
            continue
        if not _draws_shapes([drawing for drawing in drawings if rect.contains(drawing["rect"])], cluster):
            continue
        for image in over:
            rect |= image
        for other in [other for other in figures if other.intersects(rect)]:
            figures.remove(other)
            rect |= other
        figures.append(rect)
    return [rect for rect in figures if _holds_labels(blocks, rect)]


def _draws_shapes(paths: list[dict], cluster: pymupdf.Rect) -> bool:
    """Whether a cluster's paths draw more than a frame around its text: a line or a curve, or several rectangles,
    besides the paths that border or shade the whole cluster."""
    shapes = [
        path
        for path in paths
        if path["rect"].width < cluster.width * _FRAME or path["rect"].height < cluster.height * _FRAME
    ]
    rectangles = [path for path in shapes if all(item[0] in ("re", "qu") for item in path["items"])]
    return len(shapes) > len(rectangles) or len(rectangles) >= _FIGURE_SHAPES


def _holds_labels(blocks: list[dict], rect: pymupdf.Rect) -> bool:
    """Whether the text inside `rect` reads as a Figure's labels: short, and no sentence among it."""
    inside = [
        " ".join(span["text"] for line in block["lines"] for span in line["spans"]).strip()
        for block in blocks
        if block["type"] == 0 and rect.contains(_centre(block))
    ]
    sentence = any(len(text.split()) >= _SENTENCE_WORDS and text.endswith((".", "!", "?")) for text in inside)
    return sum(len(text) for text in inside) <= _FIGURE_TEXT and not sentence


def _centre(block: dict) -> pymupdf.Point:
    rect = pymupdf.Rect(block["bbox"])
    return (rect.tl + rect.br) / 2


def _save_drawing(page: pymupdf.Page, rect: pymupdf.Rect, path: Path) -> str:
    """Writes the part of the page inside `rect` as a PNG; returns its path relative to the Job folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(page.get_pixmap(dpi=_DPI, clip=rect).tobytes("png"))
    return path.relative_to(path.parent.parent).as_posix()


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


def render_page(path: Path, number: int, image: Path) -> None:
    """Writes page `number` (from 1) of the PDF at `path` as a PNG image."""
    image.write_bytes(page_image(path, number))


def page_image(path: Path, number: int, box: tuple[int, int, int, int] | None = None) -> bytes:
    """Page `number` (from 1) of the PDF at `path` as a PNG image; only the part inside `box` when given.

    `box` is x0, y0, x1, y1, normalized 0 to 1000 like a Figure's.
    """
    with pymupdf.open(path, filetype="pdf") as document:
        page = document[number - 1]
        clip = None
        if box:
            x0, y0, x1, y1 = box
            width, height = page.rect.width / 1000, page.rect.height / 1000
            clip = pymupdf.Rect(x0 * width, y0 * height, x1 * width, y1 * height)
        return page.get_pixmap(dpi=_DPI, clip=clip).tobytes("png")
