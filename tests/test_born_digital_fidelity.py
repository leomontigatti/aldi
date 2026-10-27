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


FLOWCHART_LABELS = [
    "Combined defect",
    "Thin phenotype",
    "Thick phenotype",
    "Root coverage procedure",
    "Coronally advanced flap",
    "Connective tissue graft",
    "Partial resin restoration",
    "CEJ+1mm protocol",
    "Small recession",
    "No esthetic complaint",
    "Resin composite restoration",
    "Root coverage procedure",
]


def build_flowchart_pdf(path):
    """A page with a paragraph, a decision tree drawn as boxes joined by arrows, each box a short label (well over
    200 characters in all), two of its boxes side by side like a table's cells, and its caption under it."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 60), "The decision tree guides the treatment.", fontsize=11)
    root = pymupdf.Rect(230, 90, 370, 120)
    page.draw_rect(root, color=(0, 0, 0), width=1)
    page.insert_textbox(root + (4, 4, -4, -4), FLOWCHART_LABELS[0], fontsize=8)
    boxes = []
    for index, label in enumerate(FLOWCHART_LABELS[1:]):
        row, column = divmod(index, 4)
        # The last row's first two boxes touch, like two cells of a table.
        left = 60 + column * 125 - (column == 1 and row == 2) * 15
        box = pymupdf.Rect(left, 160 + row * 70, left + 110, 200 + row * 70)
        page.draw_rect(box, color=(0, 0, 0), width=1)
        page.insert_textbox(box + (4, 4, -4, -4), label, fontsize=8)
        boxes.append(box)
    for box in boxes:
        top = ((box.x0 + box.x1) / 2, box.y0)
        page.draw_line((300, 120), top, color=(0, 0, 0), width=0.8)
        page.draw_polyline([(top[0] - 4, top[1] - 6), top, (top[0] + 4, top[1] - 6)], color=(0, 0, 0), width=0.8)
    page.insert_text((60, 400), "Figure 11. Strategies for the treatment of combined defects.", fontsize=10)
    document.save(path)
    return path


def test_a_drawn_decision_tree_with_many_short_labels_comes_out_as_one_image(app, translator, documents_dir, tmp_path):
    # Bug: a drawing holding more than 200 characters was taken for a boxed passage, so a decision tree's labels
    # came out as a jumble of paragraphs, list items and a table.
    app.wait_for(app.upload(build_flowchart_pdf(tmp_path / "Árbol.pdf"))["id"])

    docx = documents_dir / "Aldi" / "Árbol" / "Árbol (es).docx"
    assert len([name for name in zipfile.ZipFile(docx).namelist() if name.startswith("word/media/")]) == 1
    assert "<w:tbl>" not in docx_xml(docx)
    text = docx_text(docx)
    assert "ES: The decision tree guides" in text and "ES: Figure 11." in text
    sent = " ".join(text for request in translator.requests for text in request)
    assert not any(label in sent for label in FLOWCHART_LABELS)


def build_photo_grid_pdf(path):
    """A page with a paragraph and a grid of photos, as her articles print one: a single image cut into tiles by
    white bars drawn over it, each tile's panel letter in white on a small black square; the caption under it."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 60), "The case was treated in two steps.", fontsize=11)
    photo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 40), False)
    photo.set_rect(photo.irect, (200, 120, 90))
    page.insert_image(pymupdf.Rect(60, 80, 540, 320), pixmap=photo)
    white, black = (1, 1, 1), (0, 0, 0)
    page.draw_rect(pymupdf.Rect(60, 198, 540, 202), color=None, fill=white)
    for column in range(1, 5):
        page.draw_rect(pymupdf.Rect(58 + column * 96, 80, 62 + column * 96, 198), color=None, fill=white)
        page.draw_rect(pymupdf.Rect(58 + column * 96, 202, 62 + column * 96, 320), color=None, fill=white)
    for index, letter in enumerate("ABCDEFGHIJ"):
        row, column = divmod(index, 5)
        square = pymupdf.Rect(62 + column * 96, 80 + row * 122, 80 + column * 96, 98 + row * 122)
        page.draw_rect(square, color=None, fill=black)
        page.insert_text((square.x0 + 5, square.y1 - 5), letter, fontsize=9, color=white)
    page.insert_text((60, 340), "Figure 10. Representative case of a combined defect.", fontsize=10)
    document.save(path)
    return path


def test_letters_over_a_photo_grid_stay_in_the_figure_and_make_no_table(app, translator, documents_dir, tmp_path):
    # Bug: the panel letters' squares over the photos read as a table, an empty table of letters above the Figure.
    app.wait_for(app.upload(build_photo_grid_pdf(tmp_path / "Caso.pdf"))["id"])

    docx = documents_dir / "Aldi" / "Caso" / "Caso (es).docx"
    assert len([name for name in zipfile.ZipFile(docx).namelist() if name.startswith("word/media/")]) == 1
    assert "<w:tbl>" not in docx_xml(docx)
    assert "ES: Figure 10." in docx_text(docx)
    assert not any(len(text) == 1 for request in translator.requests for text in request)


def test_a_paragraph_continued_in_the_next_column_stays_a_paragraph(app, documents_dir, tmp_path):
    # Bug: the body size was the most common size of all the text, here the references set smaller, so a body
    # paragraph's last lines at the top of the next column were larger than "body" and became a heading.
    document = pymupdf.open()
    page = document.new_page()
    sentence = "The restoration was placed before the graft to rebuild the enamel junction. "
    page.insert_textbox(pymupdf.Rect(50, 60, 285, 300), sentence * 4, fontsize=10)
    page.insert_textbox(pymupdf.Rect(310, 60, 545, 100), "on the treatment of recession with lesions.", fontsize=10)
    page.insert_text((50, 330), "References", fontsize=14)
    reference = "Smith J, Muñoz A. Fluoride and caries in a cohort of children. Caries Res. 2020;54:1-9. "
    page.insert_textbox(pymupdf.Rect(50, 345, 545, 800), reference * 12, fontsize=8)
    document.save(source := tmp_path / "Columnas.pdf")

    app.wait_for(app.upload(source)["id"])

    xml = docx_xml(documents_dir / "Aldi" / "Columnas" / "Columnas (es).docx")
    (paragraph,) = [p for p in re.findall(r"<w:p[ >].*?</w:p>", xml) if "on the treatment of recession" in p]
    assert "Heading" not in paragraph


def test_a_running_header_on_one_chosen_page_is_dropped_by_the_pages_around_it(app, translator, tmp_path):
    # Bug: running headers were found by comparing the chosen pages only, and odd and even pages carry different
    # ones, so a short range kept a header as a paragraph.
    document = pymupdf.open()
    for number in range(1, 7):
        page = document.new_page()
        header = "Santamaria MP, et al." if number % 2 else "Rethinking the decision-making process"
        page.insert_text((50, 35), header, fontsize=8)
        page.insert_text((50, 100), f"Page {number} describes the enamel.", fontsize=10)
        page.insert_text((290, 815), str(number), fontsize=8)
    document.save(source := tmp_path / "Artículo.pdf")

    app.wait_for(app.upload(source, pages="3-4")["id"])

    sent = [text for request in translator.requests for text in request]
    assert sent == ["Page 3 describes the enamel.", "Page 4 describes the enamel."]


def test_the_right_column_comes_before_a_references_title_set_under_both_columns(app, translator, tmp_path):
    # Bug: the title, in the left column's width but under both columns, was read before the right column, whose
    # last paragraphs then counted as references and were left in English.
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(50, 60, 285, 200), "The left column ends the discussion. " * 3, fontsize=10)
    page.insert_textbox(pymupdf.Rect(310, 60, 545, 200), "The right column adds the outcomes.", fontsize=10)
    page.insert_text((50, 240), "References", fontsize=14)
    page.insert_textbox(pymupdf.Rect(50, 255, 545, 400), "1. Smith J. Fluoride and caries. Caries Res. 2020;54:1-9.")
    document.save(source := tmp_path / "Final.pdf")

    app.wait_for(app.upload(source)["id"])

    sent = [text for request in translator.requests for text in request]
    assert "The right column adds the outcomes." in sent
    assert not any("Smith J" in text for text in sent)


@pytest.mark.parametrize(
    "original",
    ["The lesion is shown.", "The **lesion** is shown.", "* The lesion is shown."],
    ids=["plain", "bold", "footnote star"],
)
def test_emphasis_the_translator_adds_as_escaped_marks_is_dropped_cleanly(
    app, translator, documents_dir, tmp_path, original
):
    # Bug: Gemini wrote its emphasis as `\*…\*` and a bracket as `\[`, which printed as asterisks and backslashes.
    document = pymupdf.open()
    document.new_page().insert_htmlbox(
        pymupdf.Rect(50, 60, 545, 200), "<p>" + original.replace("**lesion**", "<b>lesion</b>") + "</p>"
    )
    document.save(source := tmp_path / "Lesión.pdf")
    translate = translator.translate_texts
    answer = r"ES: la \*lesión cervical no cariosa (LCNC)\* y la unión \[\*JCE\*\]"
    translator.translate_texts = lambda texts, terms: TextTranslation(
        [answer for _text in texts], translate(texts, terms).terms
    )

    app.wait_for(app.upload(source)["id"])

    folder = documents_dir / "Aldi" / "Lesión"
    for text in (pdf_text(folder / "Lesión (es).pdf"), docx_text(folder / "Lesión (es).docx")):
        assert "ES: la lesión cervical no cariosa (LCNC) y la unión [JCE]" in text
        assert "*" not in text.replace("* ES:", "")
        assert "\\" not in text


def test_each_reference_entry_is_its_own_paragraph(app, translator, documents_dir, tmp_path):
    # Bug: the references list, one text block on the page, came out as one run-together paragraph.
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(50, 60, 545, 200), "Sealants prevent caries in the first molars.", fontsize=10)
    page.insert_text((50, 230), "References", fontsize=14)
    entries = [
        "1. Smith J, Muñoz A. Fluoride and caries in a cohort of children followed for ten years. Caries Res. "
        "2020;54:1-9.",
        "2. Corrêa M. Selantes em molares permanentes de crianças em idade escolar. Rev Odontol. 2019;12:33-40.",
        "3. Lee K. Sealants. J Dent. 2018;3:1-2.",
    ]
    page.insert_textbox(pymupdf.Rect(50, 245, 400, 600), "\n".join(entries), fontsize=8)
    document.save(source := tmp_path / "Selantes.pdf")

    app.wait_for(app.upload(source)["id"])

    paragraphs = [text for text, _item in docx_paragraphs(documents_dir / "Aldi" / "Selantes" / "Selantes (es).docx")]
    for number, entry in enumerate(entries, start=1):
        (paragraph,) = [p for p in paragraphs if p.startswith(f"{number}.")]
        assert paragraph == entry
    assert not any("Smith J" in text for request in translator.requests for text in request)


def test_a_page_range_of_references_only_keeps_them_as_untranslated_references(app, translator, tmp_path):
    # Bug: with no body text on the chosen pages the body size was 0, so every short reference became a heading,
    # translated.
    document = pymupdf.open()
    document.new_page().insert_textbox(
        pymupdf.Rect(50, 60, 545, 400), "The body text of the article is set at ten points. " * 6, fontsize=10
    )
    page = document.new_page()
    page.insert_text((50, 70), "References", fontsize=10)
    for number, y in enumerate(range(90, 200, 20), start=1):
        page.insert_text((50, y), f"{number}. Smith J. Caries {number}. Caries Res. 2020;54:{number}.", fontsize=8)
    document.save(source := tmp_path / "Referencias.pdf")

    app.wait_for(app.upload(source, pages="2")["id"])

    sent = [text for request in translator.requests for text in request]
    assert sent == ["References"]


def test_a_page_of_only_a_figure_keeps_its_caption_as_a_caption(app, documents_dir, tmp_path):
    document = pymupdf.open()
    page = document.new_page()
    photo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 60), False)
    photo.set_rect(photo.irect, (200, 120, 90))
    page.insert_image(pymupdf.Rect(100, 100, 450, 400), pixmap=photo)
    page.insert_text((100, 420), "Figure 3. A molar.", fontsize=9)
    document.save(source := tmp_path / "Figura.pdf")

    app.wait_for(app.upload(source)["id"])

    xml = docx_xml(documents_dir / "Aldi" / "Figura" / "Figura (es).docx")
    paragraphs = [p for p in re.findall(r"<w:p[ >].*?</w:p>", xml) if "ES: Figure 3." in p]
    assert not any("Heading" in paragraph for paragraph in paragraphs)
    assert any("Caption" in paragraph for paragraph in paragraphs)


def test_a_subheading_at_the_foot_of_the_left_column_is_read_there_above_a_figure(app, translator, tmp_path):
    # Bug: a single left-column line under both columns was read after the right column before anything set across
    # the page, a Figure too.
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(50, 60, 285, 200), "The left column describes the flap. " * 4, fontsize=10)
    page.insert_textbox(pymupdf.Rect(310, 60, 545, 120), "The right column describes the graft.", fontsize=10)
    page.insert_text((50, 230), "Restorative step", fontsize=12)
    photo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 60), False)
    photo.set_rect(photo.irect, (200, 120, 90))
    page.insert_image(pymupdf.Rect(50, 260, 545, 500), pixmap=photo)
    document.save(source := tmp_path / "Pasos.pdf")

    app.wait_for(app.upload(source)["id"])

    sent = [text for request in translator.requests for text in request]
    assert sent.index("Restorative step") < sent.index("The right column describes the graft.")


def test_a_ruled_table_of_symbols_stays_a_table_with_its_labels_translated(app, translator, documents_dir, tmp_path):
    # Bug: a table of `+`, `–` and `✓` cells read as panel letters, and its ruling then passed as a drawn Figure,
    # so its row labels stayed in English inside a picture.
    rows = [
        ["Technique", "1", "2", "3", "4"],
        ["Flap", "+", "–", "✓", "+"],
        ["Graft", "–", "+", "+", "✓"],
        ["Resin", "✓", "–", "+", "–"],
    ]
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 60), "The table compares the techniques.", fontsize=11)
    columns = (50, 200, 260, 320, 380, 440)
    for row_number, row in enumerate(rows):
        y = 100 + row_number * 20
        for left, right, cell in zip(columns, columns[1:], row, strict=False):
            page.draw_rect(pymupdf.Rect(left, y, right, y + 20), color=(0, 0, 0), width=0.5)
            page.insert_text((left + 4, y + 14), cell, fontsize=10, fontname="helv")
    document.save(source := tmp_path / "Tabla.pdf")

    app.wait_for(app.upload(source)["id"])

    docx = documents_dir / "Aldi" / "Tabla" / "Tabla (es).docx"
    assert not [name for name in zipfile.ZipFile(docx).namelist() if name.startswith("word/media/")]
    assert "<w:tbl>" in docx_xml(docx)
    sent = [text for request in translator.requests for text in request]
    assert {"Flap", "Graft", "Resin"} <= set(sent)
