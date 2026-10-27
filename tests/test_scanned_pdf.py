"""A Scanned PDF is read by Gemini in Page Batches and becomes a Reading Version with its figures cropped (#6)."""

import json
import re
import zipfile

import pymupdf
import pytest

from conftest import SCAN_SIZE, build_scanned_pdf, pdf_text

GREEN = (0x2E, 0x8B, 0x57)
BLUE = (0x1E, 0x40, 0xA0)
FIGURE = (100, 300, 600, 600)
REFERENCES = (100, 100, 900, 400)


def box_2d(x0, y0, x1, y1):
    """A box the way Gemini writes one: ymin, xmin, ymax, xmax."""
    return [y0, x0, y1, x1]


def pdf_images(path):
    """Each image of the PDF as a pixmap, in page order."""
    document = pymupdf.open(path)
    return [pymupdf.Pixmap(document, xref) for page in document for xref, *_ in page.get_images()]


def docx_xml(path):
    with zipfile.ZipFile(path) as docx:
        return docx.read("word/document.xml").decode("utf-8")


def docx_paragraphs(path):
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", docx_xml(path))
    return [" ".join(re.sub(r"<[^>]+>", " ", paragraph).split()) for paragraph in paragraphs]


def docx_images(path):
    with zipfile.ZipFile(path) as docx:
        return [pymupdf.Pixmap(docx.read(name)) for name in docx.namelist() if name.startswith("word/media/")]


def is_crop_of(image, box, colour):
    """Whether `image` is the part of a scanned page inside `box`, where the page was painted `colour`."""
    x0, y0, x1, y1 = box
    ratio = (x1 - x0) * SCAN_SIZE[0] / ((y1 - y0) * SCAN_SIZE[1])
    corners = [(2, 2), (image.width - 3, 2), (2, image.height - 3), (image.width - 3, image.height - 3)]
    return (
        abs(image.width / image.height - ratio) < 0.03 * ratio
        and all(tuple(image.pixel(x, y)[:3]) == colour for x, y in corners)
        and tuple(image.pixel(image.width // 2, image.height // 2)[:3]) == colour
    )


def scanned(tmp_path, pages, name="Endodoncia"):
    return build_scanned_pdf(tmp_path / f"{name}.pdf", pages)


def answering_first(translator, change):
    """Makes the first Page Batch answer `change(answer)`, a JSON text, instead of the fake's own answer."""
    read = translator.read_page_batch
    calls = []

    def read_first(pages, terms):
        answer = read(pages, terms)
        if calls:
            return answer
        calls.append(answer)
        return change(json.loads(answer))

    translator.read_page_batch = read_first


def assert_each_page_in_its_place(text, pages):
    """Every page's Spanish comes once, after its own Page Marker and before the next one."""
    order = [part for number in pages for part in (f"p. {number} del original", f"ES: Page {number}")]
    assert [text.index(part) for part in order] == sorted(text.index(part) for part in order)
    assert all(text.count(f"ES: Page {number}") == 1 for number in pages)


@pytest.fixture
def book(app, translator, documents_dir, tmp_path):
    """A six-page scanned book chapter translated through the app: its Spanish PDF and DOCX."""
    translator.scanned[1] = [
        {"kind": "furniture", "text": "Capítulo 3", "box_2d": box_2d(100, 20, 400, 50)},
        {"kind": "heading", "text": "ES: Tratamiento de conductos"},
        {"kind": "paragraph", "text": "ES: La **pulpa** está *inflamada*."},
        {"kind": "figure", "box_2d": box_2d(*FIGURE)},
        {"kind": "caption", "text": "ES: Figura 3.1. Radiografía periapical."},
        {"kind": "paragraph", "text": "ES: Page 1"},
        {"kind": "furniture", "text": "87", "box_2d": box_2d(450, 950, 550, 980)},
    ]
    translator.scanned[6] = [
        {"kind": "heading", "text": "ES: Referencias"},
        {"kind": "reference", "box_2d": box_2d(*REFERENCES)},
        {"kind": "paragraph", "text": "ES: Page 6"},
    ]
    pdf = scanned(tmp_path, [[(FIGURE, GREEN)], [], [], [], [], [(REFERENCES, BLUE)]])
    job = app.wait_for(app.upload(pdf)["id"])
    assert job["state"] == "done"
    assert job["failed_pages"] == []
    folder = documents_dir / "Aldi" / "Endodoncia"
    return folder / "Endodoncia (es).pdf", folder / "Endodoncia (es).docx"


def test_a_scanned_pdf_is_read_in_page_batches_of_four(book, translator):
    assert translator.batches == [[1, 2, 3, 4], [5, 6]]
    assert translator.requests == []


def test_every_page_arrives_in_its_place_with_its_markers(book):
    pdf, docx = book

    for text in (pdf_text(pdf), " ".join(docx_paragraphs(docx))):
        assert_each_page_in_its_place(text, range(1, 7))
        assert "ES: Tratamiento de conductos" in text


def test_the_figure_is_cropped_from_the_page_with_its_caption_under_it(book):
    pdf, docx = book

    for images in (pdf_images(pdf), docx_images(docx)):
        assert len(images) == 2
        assert is_crop_of(images[0], FIGURE, GREEN)
    paragraphs = docx_paragraphs(docx)
    caption = paragraphs.index("ES: Figura 3.1. Radiografía periapical.")
    assert paragraphs.index("ES: La pulpa está inflamada .") < caption < paragraphs.index("ES: Page 1")
    assert "<w:drawing>" in re.findall(r"<w:p[ >].*?</w:p>", docx_xml(docx))[caption - 1]


def test_the_references_are_an_image_cropped_from_the_page(book):
    pdf, docx = book

    for images in (pdf_images(pdf), docx_images(docx)):
        assert is_crop_of(images[1], REFERENCES, BLUE)
    paragraphs = docx_paragraphs(docx)
    assert paragraphs.index("ES: Referencias") < paragraphs.index("ES: Page 6")


def test_running_headers_and_page_numbers_are_dropped(book):
    pdf, docx = book

    for text in (pdf_text(pdf), " ".join(docx_paragraphs(docx))):
        assert "Capítulo 3" not in text
        assert "87" not in text


def test_bold_and_italic_come_through(book):
    _pdf, docx = book

    runs = re.findall(r"<w:r>.*?</w:r>", docx_xml(docx))
    assert any("pulpa" in run and re.search(r"<w:b\s*/>", run) for run in runs)
    assert any("inflamada" in run and re.search(r"<w:i\s*/>", run) for run in runs)


def without_page(number):
    def change(answer):
        answer["pages"] = [page for page in answer["pages"] if page["page"] != number]
        return json.dumps(answer)

    return change


def merging(first, second):
    def change(answer):
        pages = {page["page"]: page for page in answer["pages"]}
        pages[first]["blocks"] += pages.pop(second)["blocks"]
        answer["pages"] = list(pages.values())
        return json.dumps(answer)

    return change


@pytest.mark.parametrize(
    "change",
    [without_page(3), merging(2, 3), lambda answer: json.dumps(answer)[:40], lambda answer: "Lo siento, no puedo."],
    ids=["fewer-pages", "merged-pages", "cut-json", "not-json"],
)
def test_a_batch_answer_that_does_not_match_the_pages_is_asked_again_a_page_at_a_time(
    app, translator, documents_dir, tmp_path, change
):
    answering_first(translator, change)

    job = app.wait_for(app.upload(scanned(tmp_path, [[], [], [], []]))["id"])

    assert job["state"] == "done"
    assert job["failed_pages"] == []
    assert translator.batches == [[1, 2, 3, 4], [1], [2], [3], [4]]
    folder = documents_dir / "Aldi" / "Endodoncia"
    assert_each_page_in_its_place(pdf_text(folder / "Endodoncia (es).pdf"), range(1, 5))
    assert_each_page_in_its_place(" ".join(docx_paragraphs(folder / "Endodoncia (es).docx")), range(1, 5))


def test_a_batch_answer_with_its_pages_out_of_order_is_not_taken(app, translator, documents_dir, tmp_path):
    answering_first(translator, lambda answer: json.dumps({**answer, "pages": answer["pages"][::-1]}))

    app.wait_for(app.upload(scanned(tmp_path, [[], []]))["id"])

    assert translator.batches == [[1, 2], [1], [2]]
    assert_each_page_in_its_place(pdf_text(documents_dir / "Aldi" / "Endodoncia" / "Endodoncia (es).pdf"), [1, 2])


def test_an_empty_spanish_block_is_left_out(app, translator, documents_dir, tmp_path):
    translator.scanned[2] = [
        {"kind": "heading", "text": ""},
        {"kind": "paragraph", "text": "ES: Page 2"},
        {"kind": "list_item", "text": "  "},
        {"kind": "table", "rows": []},
    ]

    job = app.wait_for(app.upload(scanned(tmp_path, [[], [], []]))["id"])

    assert job["state"] == "done"
    assert translator.batches == [[1, 2, 3]]
    paragraphs = docx_paragraphs(documents_dir / "Aldi" / "Endodoncia" / "Endodoncia (es).docx")
    page_two = paragraphs[paragraphs.index("p. 2 del original") + 1 : paragraphs.index("p. 3 del original")]
    assert page_two == ["ES: Page 2"]


@pytest.mark.parametrize(
    ("painted", "answered"),
    [
        (FIGURE, [600, 600, 300, 100]),
        ((500, 300, 1000, 600), box_2d(500, 300, 1300, 600)),
        ((0, 700, 400, 1000), box_2d(-50, 700, 400, 1200)),
    ],
    ids=["inverted", "past-the-right-edge", "past-the-left-and-bottom-edges"],
)
def test_a_figure_box_inverted_or_past_the_page_is_cropped_inside_the_page(
    app, translator, documents_dir, tmp_path, painted, answered
):
    translator.scanned[1] = [{"kind": "figure", "box_2d": answered}, {"kind": "paragraph", "text": "ES: Page 1"}]

    job = app.wait_for(app.upload(scanned(tmp_path, [[(painted, GREEN)]]))["id"])

    assert job["state"] == "done"
    assert job["failed_pages"] == []
    (image,) = pdf_images(documents_dir / "Aldi" / "Endodoncia" / "Endodoncia (es).pdf")
    assert is_crop_of(image, painted, GREEN)


def test_a_figure_box_off_the_page_has_the_page_read_again(app, translator, documents_dir, tmp_path):
    def off_the_page(answer):
        answer["pages"][0]["blocks"] = [{"kind": "figure", "box_2d": box_2d(1100, 1100, 1300, 1300)}]
        return json.dumps(answer)

    translator.scanned[1] = [{"kind": "figure", "box_2d": box_2d(*FIGURE)}, {"kind": "paragraph", "text": "ES: Page 1"}]
    answering_first(translator, off_the_page)

    job = app.wait_for(app.upload(scanned(tmp_path, [[(FIGURE, GREEN)], []]))["id"])

    assert job["failed_pages"] == []
    assert translator.batches == [[1, 2], [1], [2]]
    (image,) = pdf_images(documents_dir / "Aldi" / "Endodoncia" / "Endodoncia (es).pdf")
    assert is_crop_of(image, FIGURE, GREEN)


def test_a_page_whose_answer_never_fits_becomes_a_failed_page(app, translator, documents_dir, tmp_path):
    translator.scanned[1] = [{"kind": "reference", "box_2d": [0, 0, 0, 0]}]

    job = app.wait_for(app.upload(scanned(tmp_path, [[], []]))["id"])

    assert job["state"] == "done"
    assert job["failed_pages"] == [1]
    assert translator.batches == [[1, 2], [1], [1], [1], [2]]
    text = pdf_text(documents_dir / "Aldi" / "Endodoncia" / "Endodoncia (es).pdf")
    assert text.index("p. 1 del original") < text.index("Esta página no se pudo traducir") < text.index("ES: Page 2")
