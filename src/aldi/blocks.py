"""The block model: a page's content in reading order, as the source reads it and the writer writes it."""

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Kind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    CAPTION = "caption"
    """A Figure's caption: the writer puts it under the Figure it follows."""
    TABLE = "table"
    REFERENCE = "reference"
    """An entry of the references list: never translated."""
    FURNITURE = "furniture"
    """A running header or footer, or a page number: never translated nor written."""
    FIGURE = "figure"


TRANSLATED = {Kind.HEADING, Kind.PARAGRAPH, Kind.LIST_ITEM, Kind.CAPTION, Kind.TABLE}


@dataclass(frozen=True)
class Block:
    kind: Kind
    text: str = ""
    """Inline Markdown: `**bold**` and `*italic*`, a literal `*` or `\\` escaped with `\\`, every other character
    as printed."""
    rows: list[list[str]] = field(default_factory=list)
    """A table's cells, row by row, the first row its header; each cell inline Markdown like `text`."""
    image: str = ""
    """A Figure's image file, relative to the Job folder; for a scanned page's references, their crop of the page."""
    box: tuple[int, int, int, int] | None = None
    """Where a Figure or a scanned page's references sit on the original page: x0, y0, x1, y1, normalized 0-1000."""

    @classmethod
    def from_dict(cls, data: dict) -> "Block":
        """The block `dataclasses.asdict` saved, as read back from JSON."""
        box = data.get("box")
        return cls(**{**data, "kind": Kind(data["kind"]), "box": tuple(box) if box else None})


def captions_under_figures(blocks: list[Block]) -> list[Block]:
    """A Figure's caption printed above it moves below it, where the writer looks for it."""
    blocks = list(blocks)
    for index in range(len(blocks) - 1):
        if blocks[index].kind == Kind.CAPTION and blocks[index + 1].kind == Kind.FIGURE:
            blocks[index], blocks[index + 1] = blocks[index + 1], blocks[index]
    return blocks


_EMPHASIS = re.compile(r"(\\.)|\*")


def has_emphasis(text: str) -> bool:
    return any(match.group(1) is None for match in _EMPHASIS.finditer(text))


def without_emphasis(text: str) -> str:
    """`text` with its emphasis markers removed and its escapes kept."""
    return _EMPHASIS.sub(lambda match: match.group(1) or "", text)


def plain(text: str) -> str:
    """`text` as printed: no emphasis markers, no escapes."""
    return re.sub(r"\\(.)", r"\1", without_emphasis(text))
