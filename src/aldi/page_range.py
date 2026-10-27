"""The Page Range: the pages she types (`130-145`, `3, 7-9`) as the original's page numbers."""

import re
from collections.abc import Sequence

_PART = re.compile(r"(\d+)(?:\s*[-–]\s*(\d+))?")


class PageRangeError(ValueError):
    """A Page Range that names no pages of the document; its message is the Spanish she is shown."""


def parse_page_range(text: str, page_count: int) -> list[int]:
    """The page numbers `text` names, in order and once each; every page of the document when it is empty."""
    if not text.strip():
        return list(range(1, page_count + 1))
    pages = set()
    for part in text.split(","):
        match = _PART.fullmatch(part.strip())
        if not match:
            raise PageRangeError("No se entiende qué páginas son. Escribe, por ejemplo, 130-145 o 3, 7-9.")
        first, last = int(match[1]), int(match[2] or match[1])
        if first > last:
            raise PageRangeError("No se entiende qué páginas son. Escribe, por ejemplo, 130-145 o 3, 7-9.")
        if first < 1 or last > page_count:
            raise PageRangeError(
                f"El documento tiene {page_count} páginas: elige páginas entre 1 y {page_count}."
                if page_count > 1
                else "El documento tiene una sola página."
            )
        pages.update(range(first, last + 1))
    return sorted(pages)


def format_page_range(pages: Sequence[int], page_count: int) -> str:
    """The shortest text `parse_page_range` reads back as `pages`: empty for the whole document."""
    if list(pages) == list(range(1, page_count + 1)):
        return ""
    runs: list[list[int]] = []
    for page in pages:
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return ", ".join(str(first) if first == last else f"{first}-{last}" for first, last in runs)
