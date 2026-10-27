"""A Born-digital PDF's Reading Version keeps its figures, tables, emphasis and references (#5)."""

import re
import zipfile

import pymupdf
import pytest

from conftest import (
    ARTICLE_FIGURE_COLOR,
    ARTICLE_FIGURE_LABEL,
    ARTICLE_HEADER,
    ARTICLE_REFERENCES,
    ARTICLE_TABLE,
    build_article_pdf,
)


def docx_xml(path):
    with zipfile.ZipFile(path) as docx:
        return docx.read("word/document.xml").decode("utf-8")


def docx_text(path):
    return " ".join(re.sub(r"<[^>]+>", " ", docx_xml(path)).split())


def docx_runs(path):
    """Each text run of the DOCX as (text, bold, italic)."""
    runs = []
    for run in re.findall(r"<w:r>.*?</w:r>", docx_xml(path)):
        text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", run))
        if text:
            runs.append((text, bool(re.search(r"<w:b\s*/>", run)), bool(re.search(r"<w:i\s*/>", run))))
    return runs


# Ligatures (`ﬁ`) extracted as the letters they join.
TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP


def pdf_spans(path):
    """Each text span of the PDF in page order, with its page index, box, and bold and italic flags."""
    spans = []
    for index, page in enumerate(pymupdf.open(path)):
        for block in page.get_text("dict", flags=TEXT_FLAGS)["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"].strip():
                        bold = bool(span["flags"] & pymupdf.TEXT_FONT_BOLD) or "bold" in span["font"].lower()
                        italic = bool(span["flags"] & pymupdf.TEXT_FONT_ITALIC) or "italic" in span["font"].lower()
                        spans.append((span["text"], index, pymupdf.Rect(span["bbox"]), bold, italic))
    return spans


def pdf_text(path):
    return " ".join("".join(page.get_text(flags=TEXT_FLAGS) for page in pymupdf.open(path)).split())


@pytest.fixture
def article(app, documents_dir, tmp_path):
    """The built article, translated through the app: its Spanish PDF and DOCX."""
    job = app.wait_for(app.upload(build_article_pdf(tmp_path / "Caries infantil.pdf"))["id"])
    assert job["state"] == "done"
    folder = documents_dir / "Aldi" / "Caries infantil"
    return folder / "Caries infantil (es).pdf", folder / "Caries infantil (es).docx"


def test_the_figure_sits_by_its_text_with_the_translated_caption_below(article):
    pdf, docx = article

    (page_index, image_box), *others = [
        (index, pymupdf.Rect(info["bbox"]))
        for index, page in enumerate(pymupdf.open(pdf))
        for info in page.get_image_info(xrefs=True)
    ]
    assert others == []
    image = pymupdf.open(pdf).extract_image(pymupdf.open(pdf)[page_index].get_images()[0][0])
    pixel = pymupdf.Pixmap(image["image"]).pixel(10, 10)
    assert tuple(pixel[:3]) == ARTICLE_FIGURE_COLOR
    spans = {text: (index, box) for text, index, box, *_ in pdf_spans(pdf)}
    caption = next(text for text in spans if "Figure 1" in text)
    assert caption.startswith("ES: Figure 1. Radiograph")
    assert spans[caption][0] == page_index and spans[caption][1].y0 >= image_box.y1
    text = pdf_text(pdf)
    assert text.index("ES: Children in the") < text.index("ES: Figure 1.") < text.index("ES: The right column")

    xml = docx_xml(docx)
    assert "word/media/" in "".join(zipfile.ZipFile(docx).namelist())
    assert xml.index("ES: Children in the") < xml.index("<w:drawing>") < xml.index("ES: Figure 1.")
    assert xml.index("ES: Figure 1.") < xml.index("ES: The right column")


def test_text_printed_over_a_figure_is_left_to_the_figure(article, translator):
    pdf, docx = article

    for text in (pdf_text(pdf), docx_text(docx)):
        assert ARTICLE_FIGURE_LABEL not in text
    assert not any(ARTICLE_FIGURE_LABEL in sent for request in translator.requests for sent in request)


def test_the_two_columns_are_read_left_column_first(article):
    pdf, docx = article

    for text in (pdf_text(pdf), docx_text(docx)):
        order = ["ES: Early childhood caries", "ES: Dental caries", "ES: Children in the", "ES: The right column"]
        assert [text.index(part) for part in order] == sorted(text.index(part) for part in order)


def test_a_table_stays_a_table_in_both_files(article):
    pdf, docx = article

    rows = re.findall(r"<w:tr[ >].*?</w:tr>", docx_xml(docx))
    cells = [
        [" ".join(re.sub(r"<[^>]+>", " ", cell).split()) for cell in re.findall(r"<w:tc>.*?</w:tc>", row)]
        for row in rows
    ]
    assert cells == [[f"ES: {cell}" for cell in row] for row in ARTICLE_TABLE]

    spans = {text.strip(): box for text, _index, box, *_ in pdf_spans(pdf)}
    for row in ARTICLE_TABLE:
        left, right = spans[f"ES: {row[0]}"], spans[f"ES: {row[1]}"]
        assert abs(left.y0 - right.y0) < 2 and left.x1 < right.x0
    tops = [spans[f"ES: {row[0]}"].y0 for row in ARTICLE_TABLE]
    assert tops == sorted(tops)


def test_bold_and_italic_survive_and_none_is_invented(article):
    pdf, docx = article

    for runs in (docx_runs(docx), [(text, bold, italic) for text, _i, _b, bold, italic in pdf_spans(pdf)]):
        styled = {text.strip(): (bold, italic) for text, bold, italic in runs if bold or italic}
        assert styled.pop("biofilm-mediated") == (True, False)
        assert styled.pop("mineralized") == (False, True)
        assert styled.pop("Caries Res.") == (False, True)
        # What is left is the Reading Version's own: its Page Markers and headings.
        own = ("del original", "Early childhood caries", "References")
        assert all(any(part in text for part in own) for text in styled), styled


def test_the_translator_cannot_add_emphasis_the_original_lacks(app, translator, documents_dir, tmp_path):
    translate = translator.translate_texts
    translator.translate_texts = lambda texts, terms: translate(
        [text if "*" in text else f"**{text}**" for text in texts], terms
    )

    app.wait_for(app.upload(build_article_pdf(tmp_path / "Caries.pdf"))["id"])

    runs = docx_runs(documents_dir / "Aldi" / "Caries" / "Caries (es).docx")
    bold = {text.strip() for text, is_bold, _italic in runs if is_bold}
    assert "biofilm-mediated" in bold
    assert not any("Children in the" in text or "Radiograph" in text for text in bold)


def test_the_references_are_reproduced_untranslated(article, translator):
    pdf, docx = article

    for text in (pdf_text(pdf), docx_text(docx)):
        assert "ES: References" in text
        for reference in ARTICLE_REFERENCES:
            assert reference in text
            assert f"ES: {reference}" not in text
    assert not any("Smith J" in sent for request in translator.requests for sent in request)


def test_running_headers_and_page_numbers_are_dropped(article):
    pdf, docx = article

    paragraphs = [
        " ".join(re.sub(r"<[^>]+>", " ", p).split()) for p in re.findall(r"<w:p[ >].*?</w:p>", docx_xml(docx))
    ]
    for texts in (paragraphs, [text.strip() for text, *_ in pdf_spans(pdf)]):
        assert not any(ARTICLE_HEADER.split(";")[0] in text for text in texts)
        assert "1" not in texts and "2" not in texts


def test_accents_and_enye_render_in_both_files(article):
    pdf, docx = article

    for text in (pdf_text(pdf), docx_text(docx)):
        assert "ES: Children in the Peñalolén cohort" in text
        assert "Muñoz" in text and "Corrêa" in text
