"""The Reading Version writer: translated pages to one Markdown, then a Typst PDF and a Pandoc DOCX."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pypandoc
import typst

from aldi.blocks import Block, Kind

# One column, left-aligned: `justify: false` keeps the text ragged, which reads more easily than justified.
_TYPST_PREAMBLE = """\
#set document(title: none)
#set page(paper: "a4", margin: 2.5cm)
#set text(lang: "es", font: "Libertinus Serif", size: 11pt)
#set par(justify: false)
#set figure(numbering: none)
"""

# Without auto identifiers pandoc writes no `<label>` per heading, which Typst rejects for non-ASCII headings.
_MARKDOWN = "markdown-auto_identifiers"
# Every Markdown special but the `*` and `\` a block's inline Markdown already carries, escaped or as emphasis.
_MARKDOWN_SPECIALS = re.compile(r"([`_{}\[\]<>#+\-!|~^$])")
_ORDERED_LIST_START = re.compile(r"^(\d+)([.)])")


_FAILED_PAGE = "Esta página no se pudo traducir"


@dataclass(frozen=True)
class TranslatedPage:
    number: int
    blocks: list[Block]
    failed_image: Path | None = None
    """For a Failed Page, the original page's image, relative to the folder the Reading Version is written in."""


def write_reading_version(pages: Sequence[TranslatedPage], folder: Path, stem: str) -> tuple[Path, Path]:
    """Writes `<stem>.md`, `<stem>.typ`, `<stem>.pdf` and `<stem>.docx` in `folder`; returns the PDF and DOCX."""
    markdown = to_markdown(pages)
    (folder / f"{stem}.md").write_text(markdown, encoding="utf-8")
    typ, pdf, docx = folder / f"{stem}.typ", folder / f"{stem}.pdf", folder / f"{stem}.docx"
    body = pypandoc.convert_text(markdown, "typst", format=_MARKDOWN)
    typ.write_text(_TYPST_PREAMBLE + "\n" + body, encoding="utf-8")
    # The fonts bundled with Typst cover Spanish and Portuguese; her system's fonts would make the PDF vary.
    typst.compile(typ, output=pdf, root=folder, ignore_system_fonts=True)
    pypandoc.convert_text(
        markdown, "docx", format=_MARKDOWN, outputfile=str(docx), extra_args=[f"--resource-path={folder}"]
    )
    return pdf, docx


def to_markdown(pages: Sequence[TranslatedPage]) -> str:
    paragraphs = []
    for page in pages:
        paragraphs.append(f"*p. {page.number} del original*")
        if page.failed_image:
            paragraphs.append(f"![]({page.failed_image.as_posix()}){{width=100%}}")
            paragraphs.append(f"**{_FAILED_PAGE}**")
        blocks = [block for block in page.blocks if block.kind != Kind.FURNITURE]
        for index, block in enumerate(blocks):
            previous = blocks[index - 1] if index else None
            following = blocks[index + 1] if index + 1 < len(blocks) else None
            if block.kind == Kind.FIGURE or block.image:
                is_figure = block.kind == Kind.FIGURE
                caption = following.text if is_figure and following and following.kind == Kind.CAPTION else ""
                paragraphs.append(_figure(block, caption))
            elif block.kind == Kind.CAPTION and previous and previous.kind == Kind.FIGURE:
                continue
            elif block.kind == Kind.TABLE:
                paragraphs.append(_table(block.rows))
            elif block.text.strip():
                paragraphs.append(_PREFIX.get(block.kind, "") + _escape(block.text))
    return "\n\n".join(paragraphs) + "\n"


_PREFIX = {Kind.HEADING: "## ", Kind.LIST_ITEM: "- "}


def _figure(block: Block, caption: str) -> str:
    # A Figure as wide on the page as it was on the original's; its caption, as Pandoc writes it, goes under it.
    x0, _y0, x1, _y1 = block.box or (0, 0, 1000, 0)
    width = max(10, min(100, round((x1 - x0) / 10)))
    alt = _escape(caption) if caption.strip() else ""
    return f"![{alt}]({block.image}){{width={width}%}}"


def _table(rows: list[list[str]]) -> str:
    columns = max(len(row) for row in rows)
    lines = []
    for index, row in enumerate(rows):
        cells = [_escape(cell) for cell in row] + [""] * (columns - len(row))
        lines.append("| " + " | ".join(cells) + " |")
        if index == 0:
            lines.append("|" + "---|" * columns)
    return "\n".join(lines)


def _escape(text: str) -> str:
    """A block's inline Markdown as a paragraph: no character in it turns into a heading, list or link."""
    escaped = _MARKDOWN_SPECIALS.sub(r"\\\1", " ".join(text.split()))
    return _ORDERED_LIST_START.sub(r"\1\\\2", escaped)
