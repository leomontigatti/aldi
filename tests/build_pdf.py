"""Writes a small Born-digital PDF, built the way the tests build theirs, for driving the app by hand."""

import sys
from pathlib import Path

from conftest import build_article_pdf

if __name__ == "__main__":
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    print(build_article_pdf(target))
