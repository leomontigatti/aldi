"""Prints what `aldi.source.read_pages` makes of a PDF's pages: each block's kind, box and text, and each text
block's font sizes. To check a rule against a real page.

    blocks.py PDF PAGES

PAGES is `3`, `1-4` or `2,5`. Her documents' text: read it here, never commit or quote it.
"""

import sys
import tempfile
from pathlib import Path

import pymupdf

from aldi.source import read_pages


def pages(spec: str) -> list[int]:
    numbers = []
    for part in spec.split(","):
        first, _, last = part.partition("-")
        numbers.extend(range(int(first), int(last or first) + 1))
    return numbers


def main(path: str, spec: str) -> None:
    numbers = pages(spec)
    with tempfile.TemporaryDirectory() as folder:
        for page in read_pages(Path(path), Path(folder), numbers):
            print(f"== page {page.number}{' (scanned)' if page.scanned else ''}")
            for block in page.blocks:
                content = block.text or (f"{len(block.rows)} rows: {block.rows}" if block.rows else block.image)
                print(f"{block.kind.value:10} {block.box} {content}")
    with pymupdf.open(path) as document:
        for number in numbers:
            print(f"== spans of page {number}")
            for block in document[number - 1].get_text("dict")["blocks"]:
                if block["type"] == 0:
                    sizes = sorted({round(span["size"], 2) for line in block["lines"] for span in line["spans"]})
                    text = " ".join(span["text"] for line in block["lines"] for span in line["spans"])
                    print(f"{[round(v) for v in block['bbox']]} {sizes} {text[:90]}")
                else:
                    print(f"{[round(v) for v in block['bbox']]} image")
            page = document[number - 1]
            print(f"== tables and drawing clusters of page {number}")
            for table in page.find_tables().tables:
                print(f"table {[round(v) for v in table.bbox]} {table.row_count}x{table.col_count}")
            for cluster in page.cluster_drawings():
                paths = [path for path in page.get_drawings() if cluster.contains(path["rect"])]
                print(f"cluster {[round(v) for v in cluster]} {len(paths)} paths")


if __name__ == "__main__":
    main(*sys.argv[1:])
