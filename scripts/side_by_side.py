"""Renders PDF pages to one PNG, side by side: the evidence a real-document check shows (docs/agents/verification.md).

    side_by_side.py OUT.png PDF:PAGES [PDF:PAGES ...]

Each argument is a PDF and the pages to take from it (`3`, `1-4`, `2,5`); every page becomes one column, in order.
Made from her documents, so OUT goes under the git-ignored `jobs/` and is never committed.
"""

import sys
from pathlib import Path

import pymupdf

HEIGHT = 1100
"""Each page's height in the PNG, in pixels."""


def pages(spec: str) -> list[int]:
    numbers = []
    for part in spec.split(","):
        first, _, last = part.partition("-")
        numbers.extend(range(int(first), int(last or first) + 1))
    return numbers


def main(out: str, *sources: str) -> None:
    images = []
    for source in sources:
        path, _, spec = source.rpartition(":")
        with pymupdf.open(path) as document:
            for number in pages(spec):
                page = document[number - 1]
                images.append(
                    page.get_pixmap(matrix=pymupdf.Matrix(HEIGHT / page.rect.height, HEIGHT / page.rect.height))
                )
    sheet = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, sum(image.width + 20 for image in images), HEIGHT), False)
    sheet.set_rect(sheet.irect, (200, 200, 200))
    x = 0
    for image in images:
        image = pymupdf.Pixmap(pymupdf.csRGB, image) if image.n != 3 or image.alpha else image
        # `copy` takes the region at the source's own coordinates: move the page to its column first.
        image.set_origin(x, 0)
        sheet.copy(image, image.irect)
        x += image.width + 20
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(out)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
