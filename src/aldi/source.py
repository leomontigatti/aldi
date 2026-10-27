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
# Pages either side of each chosen one whose margins tell running headers from a page's own text.
_NEIGHBOURS = 3
_HEADING_SIZE = 1.15
_CAPTION = re.compile(r"(fig(ure|ura)?s?\.?)\s*\d", re.IGNORECASE)
_REFERENCES_TITLE = re.compile(
    r"(\d+\.?\s*)?(references|referências|referências bibliográficas|referencias|referencias bibliográficas|"
    r"bibliography|bibliografia|bibliografía|literature cited|works cited)\s*:?",
    re.IGNORECASE,
)
_PAGE_NUMBER = re.compile(r"[\divxlc]+", re.IGNORECASE)
# A numbered references list's entry starts `1.` or `[1]`.
_REFERENCE_NUMBER = re.compile(r"\s*(?:\d{1,3}\.|\[\d{1,3}\])\s")
# How far right of the list's first line an entry's number may start: its continuation lines sit further in.
_HANGING_INDENT = 6
# A list item's printed marker: a bullet, or an enumerator such as `1.`, `1)`, `a)`, `(1)` or `ii.`.
_LIST_MARKER = re.compile(r"(?:[•◦▪]|\d{1,3}[.)]|[a-z]\)|\(\d{1,3}\)|[ivx]{1,4}\.)\s+")
_TYPST_IMAGES = {"png", "jpeg", "jpg", "gif"}
# A scan is rarely sharper than this: higher only makes the images Gemini reads and the crops larger.
_DPI = 150
# A drawing is a Figure when both its sides reach this many points (about 2 cm): rules and underlines do not.
_FIGURE_SIDE = 50
# A table at least this share of whose area lies over a photo is lines drawn on the photo.
_OVER_IMAGE = 0.5
# Fewer of a table's cells than this share holding more than a letter: panel letters, not a table.
_TABLE_CELLS = 0.25
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
        # Odd and even pages carry different running headers: a short range has its own only once.
        around = {
            index
            for page in chosen
            for index in range(page.number - _NEIGHBOURS, page.number + _NEIGHBOURS + 1)
            if 0 <= index < document.page_count
        }
        running = _running_texts([_margin_keys(document[index]) for index in sorted(around)])
    body_size = _body_size([items for _number, items in pages])
    in_references = False
    result = []
    for number, items in pages:
        blocks = []
        for item in items:
            block = _classify(item, body_size, running)
            if block.kind == Kind.HEADING:
                in_references = bool(_REFERENCES_TITLE.fullmatch(plain(block.text)))
            elif block.kind == Kind.PARAGRAPH and in_references:
                blocks += [Block(Kind.REFERENCE, text=entry) for entry in _entries(item.lines)]
                continue
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
    lines: list[dict] = field(default_factory=list)
    """The text's lines as PyMuPDF reads them."""


def _read_items(page: pymupdf.Page, folder: Path) -> list[_Item]:
    items = []
    blocks = page.get_text("dict", flags=_TEXT_FLAGS)["blocks"]
    images = [pymupdf.Rect(block["bbox"]) for block in blocks if block["type"] == 1]
    tables = _tables(page, images)
    drawn = _drawn_figures(page, blocks, list(tables))
    # A table drawn inside a Figure, such as two boxes of a decision tree side by side, is part of the Figure.
    tables = {rect: rows for rect, rows in tables.items() if not any(figure.contains(rect) for figure in drawn)}
    for rect, rows in tables.items():
        items.append(_Item(rect, page.rect, Block(Kind.TABLE, rows=rows)))
    # Text printed over a Figure (its labels and arrows) is part of it, and a table's text is in its cells.
    taken = list(tables)
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
            size = sizes.most_common(1)[0][0]
            item = _Item(rect, page.rect, Block(Kind.PARAGRAPH, text=text), size, sizes, block["lines"])
            items.append(item)
    return _reading_order(items, page.rect.width)


def _tables(page: pymupdf.Page, images: list[pymupdf.Rect]) -> dict[pymupdf.Rect, list[list[str]]]:
    """Where the page has a table, and its cells' text, row by row. A ruled grid that is no table, such as the
    frames of a drawing's panels around their letters, may be part of a drawn Figure."""
    tables = {}
    for table in page.find_tables().tables:
        rect = pymupdf.Rect(table.bbox)
        rows = [[_escape(" ".join((cell or "").split())) for cell in row] for row in table.extract()]
        if table.row_count and _is_table(rect, rows, images):
            tables[rect] = rows
    return tables


def _over_image(rect: pymupdf.Rect, images: list[pymupdf.Rect]) -> bool:
    return any((rect & image).get_area() >= rect.get_area() * _OVER_IMAGE for image in images)


def _is_table(rect: pymupdf.Rect, rows: list[list[str]], images: list[pymupdf.Rect]) -> bool:
    """Whether what `find_tables` found reads as a table: not lines drawn over a photo, such as the bars between the
    tiles of a photo grid, and with cells that hold more than a panel letter: words, numbers or symbols."""
    if _over_image(rect, images):
        return False
    cells = [plain(cell) for row in rows for cell in row]
    return sum(len(cell) > 1 or (cell != "" and not cell.isalpha()) for cell in cells) >= len(cells) * _TABLE_CELLS


def _drawn_figures(page: pymupdf.Page, blocks: list[dict], tables: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """Where the page draws a Figure in vectors (a diagram or a chart): a cluster of drawings large enough, not just
    a table's ruling, drawing more than a frame around its text, and whose text reads as a Figure's labels. A
    cluster over a bitmap takes the bitmap in: arrows and labels drawn on a photo are one Figure with it. Each box
    takes in its stroke's edge."""
    images = [pymupdf.Rect(block["bbox"]) for block in blocks if block["type"] == 1]
    drawings = page.get_drawings()
    figures: list[pymupdf.Rect] = []
    for cluster in page.cluster_drawings(drawings=drawings):
        rect = (cluster + (-2, -2, 2, 2)) & page.rect
        paths = [drawing for drawing in drawings if rect.contains(drawing["rect"])]
        if any(rect.intersects(table) and not _draws_beyond(paths, table) for table in tables):
            continue
        if rect.width >= page.rect.width * _PAGE_BORDER and rect.height >= page.rect.height * _PAGE_BORDER:
            continue
        over = [image for image in images if rect.intersects(image)]
        if not over and min(rect.width, rect.height) < _FIGURE_SIDE:
            continue
        if not _draws_shapes(paths, cluster):
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


def _draws_beyond(paths: list[dict], table: pymupdf.Rect) -> bool:
    """Whether a cluster draws more than the table's ruling: a path outside the table, or a slanted line or a curve
    (an arrow), which no table's ruling has."""
    edge = table + (-2, -2, 2, 2)
    for path in paths:
        if not edge.contains(path["rect"]):
            return True
        for item in path["items"]:
            if item[0] == "c" or (item[0] == "l" and item[1].x != item[2].x and item[1].y != item[2].y):
                return True
    return False


def _holds_labels(blocks: list[dict], rect: pymupdf.Rect) -> bool:
    """Whether the text inside `rect` reads as a Figure's labels, however many: no text block among it is a
    sentence. A boxed passage of prose stays text."""
    inside = [
        " ".join(span["text"] for line in block["lines"] for span in line["spans"]).strip()
        for block in blocks
        if block["type"] == 0 and rect.contains(_centre(block))
    ]
    return not any(len(text.split()) >= _SENTENCE_WORDS and text.endswith((".", "!", "?")) for text in inside)


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
            title = section[-1:] if _titles(section, item) else []
            ordered += sorted(section[: len(section) - len(title)], key=lambda item: (column(item), item.rect.y0))
            ordered += [*title, item]
            section = []
        else:
            section.append(item)
    return ordered + sorted(section, key=lambda item: (column(item), item.rect.y0))


def _titles(section: list[_Item], following: _Item) -> bool:
    """Whether the section's last item, in the order its items start, is the title of the text that follows it
    across the page: one line below everything in both columns, a references title or a heading, such as
    `References` over a list set across the page. A line over a Figure or a table stays in its column."""
    *others, last = section or [None]
    if not others or following.block.kind != Kind.PARAGRAPH:
        return False
    if last.rect.y0 < max(other.rect.y1 for other in others) or last.rect.height >= 2 * last.size:
        return False
    text = last.block.text
    heading = last.size >= following.size * _HEADING_SIZE or (text.startswith("**") and text.endswith("**"))
    return heading or bool(_REFERENCES_TITLE.fullmatch(plain(text)))


def _body_size(pages: list[list[_Item]]) -> float:
    """The size most of the running text is set in. Captions, the margins and a references list are often set
    smaller, and a few pages of references would otherwise outweigh the body: a references list runs from its
    title to the next text as large as the title."""
    sizes, every = Counter(), Counter()
    references = 0.0
    for items in pages:
        for item in items:
            every.update(item.sizes)
            text = plain(item.block.text)
            if _REFERENCES_TITLE.fullmatch(text):
                references = item.size
            elif references and item.size >= references:
                references = 0
            if not references and not _in_margin(item.rect, item.page) and not _CAPTION.match(text):
                sizes.update(item.sizes)
    # Pages with no body text, such as a caption alone or a references list, take their own most common size.
    return (sizes or every).most_common(1)[0][0] if sizes or every else 0


def _entries(lines: list[dict]) -> list[str]:
    """A references list's text block as its entries' inline Markdown: in a numbered list each entry starts at a
    line that starts with its number, left of the lines that continue an entry; otherwise the block is one entry."""
    left = lines[0]["bbox"][0] + _HANGING_INDENT
    entries: list[list[dict]] = []
    for line in lines:
        text = "".join(span["text"] for span in line["spans"])
        if not entries or (line["bbox"][0] <= left and _REFERENCE_NUMBER.match(text)):
            entries.append([])
        entries[-1].append(line)
    return [_inline(entry) for entry in entries]


def _in_margin(rect: pymupdf.Rect, page: pymupdf.Rect) -> bool:
    return rect.y1 <= page.height * _MARGIN or rect.y0 >= page.height * (1 - _MARGIN)


def _running_key(text: str) -> str:
    return " ".join(re.sub(r"\d+", " ", plain(text)).split()).lower()


def _margin_keys(page: pymupdf.Page) -> set[str]:
    """The page's texts in its top and bottom margins, as `_running_key` compares them."""
    return {
        _running_key(_inline(block["lines"]))
        for block in page.get_text("dict", flags=_TEXT_FLAGS)["blocks"]
        if block["type"] == 0 and _in_margin(pymupdf.Rect(block["bbox"]), page.rect)
    }


def _running_texts(pages: list[set[str]]) -> set[str]:
    """The margin texts that repeat on more than one page once their numbers are left out."""
    seen = Counter(key for keys in pages for key in keys if key)
    return {key for key, count in seen.items() if count > 1}


def _classify(item: _Item, body_size: float, running: set[str]) -> Block:
    block = item.block
    if block.kind != Kind.PARAGRAPH:
        return block
    text = plain(block.text)
    if _in_margin(item.rect, item.page) and (_PAGE_NUMBER.fullmatch(text) or _running_key(block.text) in running):
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
