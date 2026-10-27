"""The Reading Version writer: translated pages to one Markdown, then a Typst PDF and a Pandoc DOCX."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pypandoc
import typst

# One column, left-aligned: `justify: false` keeps the text ragged, which reads more easily than justified.
_TYPST_PREAMBLE = """\
#set document(title: none)
#set page(paper: "a4", margin: 2.5cm)
#set text(lang: "es", font: "Libertinus Serif", size: 11pt)
#set par(justify: false)
"""

# Without auto identifiers pandoc writes no `<label>` per heading, which Typst rejects for non-ASCII headings.
_MARKDOWN = "markdown-auto_identifiers"
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]<>#+\-!|~^$])")
_ORDERED_LIST_START = re.compile(r"^(\d+)([.)])")


@dataclass(frozen=True)
class TranslatedPage:
    number: int
    texts: list[str]


def write_reading_version(pages: Sequence[TranslatedPage], folder: Path, stem: str) -> tuple[Path, Path]:
    """Writes `<stem>.md`, `<stem>.typ`, `<stem>.pdf` and `<stem>.docx` in `folder`; returns the PDF and DOCX."""
    markdown = to_markdown(pages)
    (folder / f"{stem}.md").write_text(markdown, encoding="utf-8")
    typ, pdf, docx = folder / f"{stem}.typ", folder / f"{stem}.pdf", folder / f"{stem}.docx"
    body = pypandoc.convert_text(markdown, "typst", format=_MARKDOWN)
    typ.write_text(_TYPST_PREAMBLE + "\n" + body, encoding="utf-8")
    # The fonts bundled with Typst cover Spanish and Portuguese; her system's fonts would make the PDF vary.
    typst.compile(typ, output=pdf, root=folder, ignore_system_fonts=True)
    pypandoc.convert_text(markdown, "docx", format=_MARKDOWN, outputfile=str(docx))
    return pdf, docx


def to_markdown(pages: Sequence[TranslatedPage]) -> str:
    paragraphs = []
    for page in pages:
        paragraphs.append(f"*p. {page.number} del original*")
        paragraphs.extend(_escape(text) for text in page.texts if text.strip())
    return "\n\n".join(paragraphs) + "\n"


def _escape(text: str) -> str:
    """Plain text as a Markdown paragraph: no character Gemini returns turns into a heading, list or link."""
    escaped = _MARKDOWN_SPECIALS.sub(r"\\\1", " ".join(text.split()))
    return _ORDERED_LIST_START.sub(r"\1\\\2", escaped)
