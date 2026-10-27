"""A page that keeps failing becomes a Failed Page, and a finished Job can retry its Failed Pages (#11)."""

import re
import urllib.error
import zipfile

import pymupdf
import pytest

from aldi.translator import QuotaExhausted
from conftest import build_born_digital_pdf, pdf_text

FAILED = "Esta página no se pudo traducir"


def failing(translator, text, times, error=RuntimeError):
    """Makes `translator` raise `error` the first `times` times it is asked for `text`."""
    translate = translator.translate_texts
    failures = []

    def translate_or_fail(texts, terms):
        if text in texts and len(failures) < times:
            failures.append(text)
            raise error("Gemini answered something unreadable")
        return translate(texts, terms)

    translator.translate_texts = translate_or_fail
    return failures


def pdf_images(path):
    return sum(len(page.get_images()) for page in pymupdf.open(path))


def docx_text_and_images(path):
    with zipfile.ZipFile(path) as docx:
        xml = docx.read("word/document.xml").decode("utf-8")
        images = [name for name in docx.namelist() if name.startswith("word/media/")]
    return " ".join(re.sub(r"<[^>]+>", " ", xml).split()), images


def three_pages(tmp_path):
    pages = [[f"Page {number} is about the pulp."] for number in range(1, 4)]
    return build_born_digital_pdf(tmp_path / "Pulpa.pdf", pages)


def test_a_page_that_fails_three_times_arrives_as_its_image_with_a_message(app, translator, documents_dir, tmp_path):
    failing(translator, "Page 2 is about the pulp.", times=3)

    job = app.wait_for(app.upload(three_pages(tmp_path))["id"])

    assert job["state"] == "done"
    assert job["failed_pages"] == [2]
    folder = documents_dir / "Aldi" / "Pulpa"
    text = pdf_text(folder / "Pulpa (es).pdf")
    assert text.index("ES: Page 1") < text.index("p. 2 del original") < text.index(FAILED) < text.index("ES: Page 3")
    assert "ES: Page 2" not in text
    assert pdf_images(folder / "Pulpa (es).pdf") == 1
    docx_text, docx_images = docx_text_and_images(folder / "Pulpa (es).docx")
    assert docx_text.index("p. 2 del original") < docx_text.index(FAILED) < docx_text.index("ES: Page 3")
    assert len(docx_images) == 1
    page = tmp_path / "data" / "jobs" / job["id"] / "pages" / "0002.json"
    assert "Gemini answered something unreadable" in page.read_text(encoding="utf-8")


def test_a_page_that_fails_twice_is_translated_on_its_third_try(app, translator, documents_dir, tmp_path):
    failures = failing(translator, "Page 2 is about the pulp.", times=2)

    job = app.wait_for(app.upload(three_pages(tmp_path))["id"])

    assert len(failures) == 2
    assert job["failed_pages"] == []
    text = pdf_text(documents_dir / "Aldi" / "Pulpa" / "Pulpa (es).pdf")
    assert "ES: Page 2 is about the pulp." in text
    assert FAILED not in text


def test_retrying_translates_only_the_failed_pages_beside_the_first_results(
    app, translator, documents_dir, opened, tmp_path
):
    failing(translator, "Page 2 is about the pulp.", times=3)
    job = app.wait_for(app.upload(three_pages(tmp_path))["id"])
    translator.requests.clear()

    retried = app.send("POST", f"/api/jobs/{job['id']}/retry")
    job = app.wait_for(retried["id"])

    assert job["state"] == "done"
    assert job["failed_pages"] == []
    assert job["results"] == ["Pulpa (es) (2).pdf", "Pulpa (es) (2).docx"]
    assert translator.requests == [["Page 2 is about the pulp."]]
    folder = documents_dir / "Aldi" / "Pulpa"
    # She may have edited the first results, or have them open in Word: a retry never writes over them.
    assert sorted(path.name for path in folder.iterdir()) == [
        "Pulpa (es) (2).docx",
        "Pulpa (es) (2).pdf",
        "Pulpa (es).docx",
        "Pulpa (es).pdf",
    ]
    assert FAILED in pdf_text(folder / "Pulpa (es).pdf")
    text = pdf_text(folder / "Pulpa (es) (2).pdf")
    assert text.index("ES: Page 1") < text.index("ES: Page 2") < text.index("ES: Page 3")
    assert FAILED not in text
    assert pdf_images(folder / "Pulpa (es) (2).pdf") == 0
    docx_text, docx_images = docx_text_and_images(folder / "Pulpa (es) (2).docx")
    assert docx_text.index("ES: Page 1") < docx_text.index("ES: Page 2") < docx_text.index("ES: Page 3")
    assert docx_images == []
    assert opened == [folder, folder]


def test_a_job_without_failed_pages_has_nothing_to_retry(app, tmp_path):
    job = app.wait_for(app.upload(three_pages(tmp_path))["id"])

    with pytest.raises(urllib.error.HTTPError) as refused:
        app.send("POST", f"/api/jobs/{job['id']}/retry")

    assert refused.value.code == 409


def test_a_quota_pause_never_makes_a_failed_page(app, translator, tmp_path):
    failing(translator, "Page 2 is about the pulp.", times=3, error=QuotaExhausted)

    job = app.wait_for(app.upload(three_pages(tmp_path))["id"])

    assert job["failed_pages"] == []
    page = tmp_path / "data" / "jobs" / job["id"] / "pages" / "0002.json"
    assert not page.exists() or '"failed"' not in page.read_text(encoding="utf-8")
