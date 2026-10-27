"""A document with both kinds of page is routed page by page into one Reading Version (#12)."""

import re
import zipfile

import pymupdf
import pytest

from conftest import (
    ARTICLE_FIGURE_COLOR,
    build_article_pdf,
    build_born_digital_pdf,
    build_mixed_pdf,
    build_scanned_pdf,
    pdf_text,
)

BLUE = (0x1E, 0x40, 0xA0)
FIGURE = (100, 300, 600, 600)


def pdf_images(path):
    """The colour at the centre of each image of the PDF, in page order."""
    document = pymupdf.open(path)
    images = [pymupdf.Pixmap(document, xref) for page in document for xref, *_ in page.get_images()]
    return [tuple(image.pixel(image.width // 2, image.height // 2)[:3]) for image in images]


def docx_text(path):
    with zipfile.ZipFile(path) as docx:
        xml = docx.read("word/document.xml").decode("utf-8")
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml)
    return " ".join(" ".join(re.sub(r"<[^>]+>", " ", paragraph).split()) for paragraph in paragraphs)


def text_pages(tmp_path, name, numbers):
    return build_born_digital_pdf(tmp_path / f"{name}.pdf", [[f"Page {number}"] for number in numbers])


def scanned_pages(tmp_path, name, pages):
    return build_scanned_pdf(tmp_path / f"{name}.pdf", pages)


@pytest.fixture
def mixed(app, translator, documents_dir, tmp_path):
    """An eight-page document: pages 1, 2, 6 and 8 have a text layer, pages 3, 4, 5 and 7 are photos."""
    parts = [
        text_pages(tmp_path, "a", [1, 2]),
        scanned_pages(tmp_path, "b", [[], [], []]),
        text_pages(tmp_path, "c", [6]),
        scanned_pages(tmp_path, "d", [[]]),
        text_pages(tmp_path, "e", [8]),
    ]
    pdf = build_mixed_pdf(tmp_path / "Ortodoncia.pdf", parts)
    job = app.wait_for(app.upload(pdf)["id"])
    assert job["state"] == "done"
    assert job["failed_pages"] == []
    folder = documents_dir / "Aldi" / "Ortodoncia"
    return folder / "Ortodoncia (es).pdf", folder / "Ortodoncia (es).docx"


def test_each_page_goes_through_its_own_path(mixed, translator):
    assert translator.batches == [[3, 4, 5], [7]]
    assert translator.requests == [["Page 1"], ["Page 2"], ["Page 6"], ["Page 8"]]


def test_every_page_arrives_in_order_with_continuous_page_markers(mixed):
    pdf, docx = mixed

    for text in (pdf_text(pdf), docx_text(docx)):
        order = [part for number in range(1, 9) for part in (f"p. {number} del original", f"ES: Page {number}")]
        assert [text.index(part) for part in order] == sorted(text.index(part) for part in order)
        assert all(text.count(f"p. {number} del original") == 1 for number in range(1, 9))
        assert all(text.count(f"ES: Page {number}") == 1 for number in range(1, 9))


def test_an_article_with_a_scanned_appendix_keeps_the_figures_of_both(app, translator, documents_dir, tmp_path):
    translator.scanned[3] = [
        {"kind": "figure", "box_2d": [300, 100, 600, 600]},
        {"kind": "paragraph", "text": "ES: Anexo"},
    ]
    article = build_article_pdf(tmp_path / "article.pdf")
    appendix = scanned_pages(tmp_path, "appendix", [[(FIGURE, BLUE)]])

    job = app.wait_for(app.upload(build_mixed_pdf(tmp_path / "Caries.pdf", [article, appendix]))["id"])

    assert job["state"] == "done"
    assert translator.batches == [[3]]
    pdf = documents_dir / "Aldi" / "Caries" / "Caries (es).pdf"
    assert pdf_images(pdf) == [ARTICLE_FIGURE_COLOR, BLUE]
    text = pdf_text(pdf)
    assert text.index("p. 2 del original") < text.index("p. 3 del original") < text.index("ES: Anexo")
