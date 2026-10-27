"""A Born-digital PDF's Reading Version keeps its figures, tables, emphasis and references (#5)."""

import json
import re
import zipfile

import pymupdf
import pytest

from aldi.translator import TextTranslation
from conftest import (
    ARTICLE_FIGURE_COLOR,
    ARTICLE_FIGURE_LABEL,
    ARTICLE_HEADER,
    ARTICLE_REFERENCES,
    ARTICLE_TABLE,
    build_article_pdf,
    build_born_digital_pdf,
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


def docx_paragraphs(path):
    """Each paragraph of the DOCX as (text, whether it is a list item)."""
    paragraphs = []
    for paragraph in re.findall(r"<w:p[ >].*?</w:p>", docx_xml(path)):
        text = " ".join(re.sub(r"<[^>]+>", " ", paragraph).split())
        paragraphs.append((text, "<w:numPr>" in paragraph))
    return paragraphs


def test_a_list_stays_a_list_without_its_printed_markers(app, documents_dir, tmp_path):
    # Bug: every text block became a paragraph, so a list arrived as paragraphs starting with `•` or `1.`.
    items = ["• Brush twice a day.", "▪ Use fluoride toothpaste.", "1. Floss every night.", "a) Rinse after meals."]
    items += ["(2) Visit the dentist.", "ii. Replace the brush."]
    document = pymupdf.open()
    paragraphs = ["Oral hygiene starts at home.", *items]
    html = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
    document.new_page().insert_htmlbox(pymupdf.Rect(50, 60, 545, 750), html, css="* {margin: 0 0 8pt 0;}")
    document.save(source := tmp_path / "Higiene.pdf")

    app.wait_for(app.upload(source)["id"])

    paragraphs = dict(docx_paragraphs(documents_dir / "Aldi" / "Higiene" / "Higiene (es).docx"))
    assert paragraphs["ES: Oral hygiene starts at home."] is False
    for item in ("Brush twice a day.", "Use fluoride toothpaste.", "Floss every night.", "Rinse after meals."):
        assert paragraphs[f"ES: {item}"] is True
    assert paragraphs["ES: Visit the dentist."] is True
    assert paragraphs["ES: Replace the brush."] is True


def test_a_paragraph_that_starts_like_a_list_marker_stays_a_paragraph(app, translator, documents_dir, tmp_path):
    # Bug: Pandoc read `a)`, `i.`, `(1)` and `A.` at the start of a Spanish paragraph as a list and dropped them.
    spanish = {
        "The enamel.": "a) el esmalte es duro.",
        "The dentin.": "i. la dentina es blanda.",
        "The pulp.": "(1) la pulpa está viva.",
        "The author.": "A. Corrêa estudió la pulpa.",
    }
    translator.translate_texts = lambda texts, terms: TextTranslation([spanish[text] for text in texts], dict(terms))
    source = build_born_digital_pdf(tmp_path / "Pulpa.pdf", [list(spanish)])

    app.wait_for(app.upload(source)["id"])

    folder = documents_dir / "Aldi" / "Pulpa"
    paragraphs = dict(docx_paragraphs(folder / "Pulpa (es).docx"))
    text = pdf_text(folder / "Pulpa (es).pdf")
    for paragraph in spanish.values():
        assert paragraphs[paragraph] is False
        assert paragraph in text


def test_a_line_of_dialogue_keeps_its_dash_and_stays_a_paragraph(app, documents_dir, tmp_path):
    # Bug: `–` and `-` counted as list markers, so a Portuguese book's dialogue became bullets without its dashes.
    document = pymupdf.open()
    html = "<p>– Olá, doutora.</p><p>- Bom dia.</p>"
    document.new_page().insert_htmlbox(pymupdf.Rect(50, 60, 545, 750), html, css="* {margin: 0 0 8pt 0;}")
    document.save(source := tmp_path / "Diálogo.pdf")

    app.wait_for(app.upload(source)["id"])

    paragraphs = dict(docx_paragraphs(documents_dir / "Aldi" / "Diálogo" / "Diálogo (es).docx"))
    assert paragraphs["ES: – Olá, doutora."] is False
    assert paragraphs["ES: - Bom dia."] is False


DIAGRAM_LABEL = "Pulp chamber"


def build_diagram_pdf(path):
    """A page with a paragraph, a drawn diagram (lines, a rectangle and a label) with its caption under it, a ruled
    table and a horizontal rule across the page."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 60), "The diagram shows the parts of a molar.", fontsize=11)
    page.draw_rect(pymupdf.Rect(150, 90, 400, 260), color=(0, 0, 0), width=1)
    page.draw_line((180, 230), (275, 120), color=(0.2, 0.2, 0.8), width=2)
    page.draw_line((275, 120), (370, 230), color=(0.2, 0.2, 0.8), width=2)
    page.draw_circle((275, 200), 25, color=(0.8, 0.1, 0.1), width=2)
    page.insert_text((245, 250), DIAGRAM_LABEL, fontsize=9)
    page.insert_text((150, 280), "Figure 2. Diagram of a molar.", fontsize=10)
    top, row_height, columns = 320, 20, (50, 300, 545)
    for row_number, row in enumerate(ARTICLE_TABLE):
        y = top + row_number * row_height
        for left, right, cell in zip(columns, columns[1:], row, strict=False):
            page.draw_rect(pymupdf.Rect(left, y, right, y + row_height), color=(0, 0, 0), width=0.5)
            page.insert_text((left + 4, y + 14), cell, fontsize=10)
    page.draw_line((50, 420), (545, 420), color=(0, 0, 0), width=0.5)
    page.insert_text((50, 450), "The rule above closes the section.", fontsize=11)
    document.save(path)
    return path


def test_a_drawn_diagram_comes_out_as_one_image_with_its_caption_and_label(app, translator, documents_dir, tmp_path):
    # Bug: only bitmap images were Figures, so a diagram drawn in vectors vanished and its label became a paragraph.
    app.wait_for(app.upload(build_diagram_pdf(tmp_path / "Molar.pdf"))["id"])

    folder = documents_dir / "Aldi" / "Molar"
    pdf, docx = folder / "Molar (es).pdf", folder / "Molar (es).docx"
    media = [name for name in zipfile.ZipFile(docx).namelist() if name.startswith("word/media/")]
    assert len(media) == 1
    assert sum(len(page.get_images()) for page in pymupdf.open(pdf)) == 1
    assert len(re.findall(r"<w:tbl>", docx_xml(docx))) == 1
    text = docx_text(docx)
    assert "ES: Figure 2. Diagram of a molar." in text
    assert text.index("ES: The diagram shows") < text.index("ES: Figure 2.") < text.index("ES: Tooth")
    assert not any(DIAGRAM_LABEL in sent for request in translator.requests for sent in request)


@pytest.mark.parametrize("box", [{"fill": (0.85, 0.9, 0.95), "color": None}, {"color": (0, 0, 0), "width": 1}])
def test_a_short_passage_in_a_box_is_translated_as_text(app, translator, documents_dir, tmp_path, box):
    # Bug: any drawing 50 pt across around 200 characters or fewer was a Figure, so a callout became an untranslated
    # image.
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 60), "Fluoride protects the enamel.", fontsize=11)
    page.draw_rect(pymupdf.Rect(50, 90, 545, 160), **box)
    page.insert_text((62, 125), "Note: brush twice a day.", fontsize=11)
    document.save(source := tmp_path / "Nota.pdf")

    app.wait_for(app.upload(source)["id"])

    docx = documents_dir / "Aldi" / "Nota" / "Nota (es).docx"
    assert not [name for name in zipfile.ZipFile(docx).namelist() if name.startswith("word/media/")]
    assert "ES: Note: brush twice a day." in docx_text(docx)


PHOTO_LABEL = "Enamel"


def test_arrows_drawn_around_a_photo_come_out_with_it_as_one_image(app, translator, documents_dir, tmp_path):
    # Bug: a drawing overlapping a bitmap was dropped, so only the photo was kept and its arrows were lost.
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 60), "The photo shows a molar.", fontsize=11)
    photo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 60), False)
    photo.set_rect(photo.irect, (200, 120, 90))
    page.insert_image(pymupdf.Rect(200, 100, 350, 250), pixmap=photo)
    for tail, head in (((80, 120), (230, 160)), ((480, 230), (320, 200))):
        page.draw_line(tail, head, color=(0, 0, 0), width=1.5)
        page.draw_polyline([(head[0] - 6, head[1] - 6), head, (head[0] - 6, head[1] + 6)], color=(0, 0, 0))
    page.insert_text((90, 140), PHOTO_LABEL, fontsize=8)
    page.insert_text((50, 300), "The arrows point at the enamel.", fontsize=11)
    document.save(source := tmp_path / "Foto.pdf")

    job = app.wait_for(app.upload(source)["id"])

    docx = documents_dir / "Aldi" / "Foto" / "Foto (es).docx"
    assert len([name for name in zipfile.ZipFile(docx).namelist() if name.startswith("word/media/")]) == 1
    blocks = json.loads((tmp_path / "data" / "jobs" / job["id"] / "pages" / "0001.json").read_text("utf-8"))["blocks"]
    (figure,) = [block for block in blocks if block["kind"] == "figure"]
    # In points, give or take the rounding of a box normalized to 0-1000.
    x0, y0, x1, y1 = (value * length / 1000 for value, length in zip(figure["box"], [*page.rect.br] * 2, strict=True))
    assert x0 <= 81 and y0 <= 101 and x1 >= 479 and y1 >= 249
    assert not any(PHOTO_LABEL in sent for request in translator.requests for sent in request)
