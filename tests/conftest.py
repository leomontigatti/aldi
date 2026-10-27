"""Test support: the fake translator, the builders of test PDFs, and the app running on a free port."""

import json
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path

import pymupdf
import pytest

from aldi.app import create_server
from aldi.translator import TextTranslation


class FakeTranslator:
    """Stands in for Gemini at the translator seam: each text comes back prefixed with `ES:`."""

    def __init__(self):
        self.requests: list[list[str]] = []

    def translate_texts(self, texts: Sequence[str], terms: Mapping[str, str]) -> TextTranslation:
        self.requests.append(list(texts))
        return TextTranslation(texts=[f"ES: {text}" for text in texts], terms=dict(terms))


def build_born_digital_pdf(path: Path, pages: Sequence[Sequence[str]]) -> Path:
    """A PDF with a text layer: each page holds the given paragraphs, one text block each."""
    document = pymupdf.open()
    for paragraphs in pages:
        page = document.new_page()
        y = 72
        for paragraph in paragraphs:
            box = pymupdf.Rect(72, y, page.rect.width - 72, y + 200)
            page.insert_textbox(box, paragraph, fontsize=11)
            y += 220
    document.save(path)
    return path


ARTICLE_HEADER = "Journal of Oral Science 2026; 12:"
ARTICLE_FIGURE_COLOR = (0x2E, 0x8B, 0x57)
ARTICLE_FIGURE_LABEL = "Occlusal lesion"
ARTICLE_TABLE = [["Tooth", "Eruption age"], ["Central incisor", "7 years"], ["First molar", "6 years"]]
ARTICLE_REFERENCES = [
    "1. Smith J, Muñoz A. Fluoride and caries. Caries Res. 2020;54:1-9.",
    "2. Corrêa M. Selantes em molares. Rev Odontol. 2019;12:33-40.",
]


def build_article_pdf(path: Path) -> Path:
    """A two-page journal article with a text layer, laid out the way her articles are.

    Page 1: a running header and page number, a title across the page, then two columns: on the left two
    paragraphs with bold and italic runs, on the right an image with a label printed over it, its caption under
    it and a paragraph; under both columns, a ruled table across the page. Page 2: one paragraph, the `References`
    heading (bold, at body size) and a references list with italic journal names.
    """
    document = pymupdf.open()
    css = "* {font-size: 10pt; margin: 0 0 8pt 0;}"
    for number in (1, 2):
        page = document.new_page()
        page.insert_text((50, 35), f"{ARTICLE_HEADER}{number}", fontsize=8)
        page.insert_text((290, 815), str(number), fontsize=8)
    first, second = document[0], document[1]
    first.insert_textbox(pymupdf.Rect(50, 60, 545, 100), "Early childhood caries: a review", fontsize=18)
    first.insert_htmlbox(
        pymupdf.Rect(50, 110, 285, 500),
        "<p>Dental caries is a <b>biofilm-mediated</b> disease of the <i>mineralized</i> tissues.</p>"
        "<p>Children in the Peñalolén cohort were examined every year.</p>",
        css=css,
    )
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), False)
    image.set_rect(image.irect, ARTICLE_FIGURE_COLOR)
    first.insert_image(pymupdf.Rect(310, 110, 545, 250), stream=image.tobytes("png"))
    first.insert_text((330, 180), ARTICLE_FIGURE_LABEL, fontsize=9)
    first.insert_htmlbox(
        pymupdf.Rect(310, 258, 545, 500),
        "<p>Figure 1. Radiograph of a carious molar.</p><p>The right column continues the text.</p>",
        css=css,
    )
    top, row_height, columns = 520, 20, (50, 300, 545)
    for row_number, row in enumerate(ARTICLE_TABLE):
        y = top + row_number * row_height
        for left, right, cell in zip(columns, columns[1:], row, strict=False):
            first.draw_rect(pymupdf.Rect(left, y, right, y + row_height), color=(0, 0, 0), width=0.5)
            first.insert_text((left + 4, y + 14), cell, fontsize=10)
    second.insert_htmlbox(
        pymupdf.Rect(50, 60, 545, 750),
        "<p>Sealants prevent caries in the first molars.</p><p><b>References</b></p>"
        + "".join(f"<p>{reference}</p>" for reference in ARTICLE_REFERENCES).replace(
            "Caries Res.", "<i>Caries Res.</i>"
        ),
        css=css,
    )
    document.save(path)
    return path


class RunningApp:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def get(self, path: str):
        with urllib.request.urlopen(self.base_url + path) as response:
            return json.load(response)

    def upload(self, pdf: Path, filename: str | None = None):
        request = urllib.request.Request(
            self.base_url + "/api/jobs",
            data=pdf.read_bytes(),
            method="POST",
            headers={"X-Filename": urllib.parse.quote(filename or pdf.name)},
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def wait_for(self, job_id: str, timeout: float = 60) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(f"/api/jobs/{job_id}")
            if job["state"] in ("done", "failed"):
                return job
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not finish: {job}")


@pytest.fixture
def translator():
    return FakeTranslator()


@pytest.fixture
def documents_dir(tmp_path):
    return tmp_path / "Documentos"


@pytest.fixture
def app(tmp_path, translator, documents_dir):
    server = create_server(
        data_dir=tmp_path / "data", documents_dir=documents_dir, translator=translator, host="127.0.0.1", port=0
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield RunningApp(f"http://{host}:{port}")
    server.shutdown()
    server.server_close()
