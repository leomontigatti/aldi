"""She chooses the pages before a Job starts, sees how long it will take, and follows it page by page (#9)."""

import json
import urllib.error
import urllib.parse

import pymupdf
import pytest
from test_jobs import closing_on

from aldi.estimate import estimate
from aldi.page_range import PageRangeError, parse_page_range
from conftest import FakeTranslator, build_born_digital_pdf, build_scanned_pdf


def five_pages(tmp_path):
    pages = [[f"Page {number} is about the pulp."] for number in range(1, 6)]
    return build_born_digital_pdf(tmp_path / "Pulpa.pdf", pages)


def pdf_text(path):
    return " ".join("".join(page.get_text() for page in pymupdf.open(path)).split())


def refused(call) -> tuple[int, str]:
    with pytest.raises(urllib.error.HTTPError) as error:
        call()
    return error.value.code, json.load(error.value)["error"]


@pytest.mark.parametrize(
    ("text", "pages"),
    [
        ("", list(range(1, 201))),
        ("   ", list(range(1, 201))),
        ("130-145", list(range(130, 146))),
        ("3, 7-9", [3, 7, 8, 9]),
        (" 7 - 9 ,3 ", [3, 7, 8, 9]),
        ("7–9", [7, 8, 9]),
        ("3,3, 2-4", [2, 3, 4]),
        ("200", [200]),
        ("1-200", list(range(1, 201))),
    ],
)
def test_the_page_range_takes_ranges_lists_and_both(text, pages):
    assert parse_page_range(text, 200) == pages


@pytest.mark.parametrize("text", ["0", "201", "190-210", "0-3"])
def test_a_page_range_outside_the_document_is_refused_in_spanish(text):
    with pytest.raises(PageRangeError) as refused:
        parse_page_range(text, 200)

    assert str(refused.value) == "El documento tiene 200 páginas: elige páginas entre 1 y 200."


@pytest.mark.parametrize("text", ["abc", "3-", "-3", "1.5", "3,,4", "3 4", "9-7", "capítulo 2"])
def test_a_malformed_page_range_is_refused_in_spanish(text):
    with pytest.raises(PageRangeError) as refused:
        parse_page_range(text, 200)

    assert str(refused.value) == "No se entiende qué páginas son. Escribe, por ejemplo, 130-145 o 3, 7-9."


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        (1, "Tiempo estimado: un minuto"),
        (12, "Tiempo estimado: unos 4 minutos"),
        (120, "Tiempo estimado: unos 40 minutos"),
        (170, "Tiempo estimado: una hora"),
        (270, "Tiempo estimado: una hora y media"),
        (400, "Tiempo estimado: unas 2 horas"),
        (480, "Tiempo estimado: unas 2 horas y media"),
    ],
)
def test_the_estimate_is_minutes_or_hours_never_requests(pages, expected):
    assert estimate([False] * pages) == expected


def test_the_estimate_depends_on_the_kind_of_pages():
    assert estimate([True] * 120) != estimate([False] * 120)


def test_a_dropped_document_waits_for_its_pages_and_shows_its_estimate(app, translator, tmp_path):
    document = app.drop(five_pages(tmp_path))

    assert document["name"] == "Pulpa"
    assert document["pages"] == 5
    assert document["estimate"] == estimate([False] * 5)
    chosen = app.get(f"/api/documents/{document['id']}/estimate?pages=" + urllib.parse.quote("2, 4-5"))
    assert chosen == {
        "pages": 3,
        "estimate": estimate([False] * 3),
        "layout_estimate": estimate([False] * 3, layout=True),
    }
    assert app.get("/api/jobs")["jobs"] == []
    assert translator.requests == []


def test_a_wrong_page_range_is_refused_before_anything_starts(app, translator, tmp_path):
    document = app.drop(five_pages(tmp_path))

    code, message = refused(lambda: app.get(f"/api/documents/{document['id']}/estimate?pages=6"))
    assert (code, message) == (400, "El documento tiene 5 páginas: elige páginas entre 1 y 5.")
    code, message = refused(lambda: app.send("POST", "/api/jobs", {"document": document["id"], "pages": "2 a 4"}))
    assert (code, message) == (400, "No se entiende qué páginas son. Escribe, por ejemplo, 130-145 o 3, 7-9.")
    assert app.get("/api/jobs")["jobs"] == []

    job = app.wait_for(app.send("POST", "/api/jobs", {"document": document["id"], "pages": "2-4"})["id"])
    assert job["state"] == "done"


def test_a_document_cannot_be_started_twice(app, tmp_path):
    document = app.drop(five_pages(tmp_path))
    app.send("POST", "/api/jobs", {"document": document["id"], "pages": ""})

    code, _message = refused(lambda: app.send("POST", "/api/jobs", {"document": document["id"], "pages": ""}))

    assert code == 404
    assert len(app.get("/api/jobs")["jobs"]) == 1


def test_only_the_chosen_pages_are_translated_and_keep_their_original_numbers(app, translator, documents_dir, tmp_path):
    job = app.wait_for(app.upload(five_pages(tmp_path), pages="2, 4-5")["id"])

    assert job["state"] == "done"
    assert (job["page"], job["pages"], job["page_range"]) == (3, 3, "2, 4-5")
    assert translator.requests == [[f"Page {number} is about the pulp."] for number in (2, 4, 5)]
    text = pdf_text(documents_dir / "Aldi" / "Pulpa" / "Pulpa (es).pdf")
    for number in (2, 4, 5):
        assert f"p. {number} del original" in text
    for number in (1, 3):
        assert f"p. {number} del original" not in text
        assert f"Page {number} is" not in text


def test_the_chosen_pages_of_a_scanned_pdf_are_read_in_page_batches(app, translator, tmp_path):
    scanned = build_scanned_pdf(tmp_path / "Libro.pdf", [[] for _ in range(8)])

    job = app.wait_for(app.upload(scanned, pages="2-6")["id"])

    assert job["state"] == "done"
    assert translator.batches == [[2, 3, 4, 5], [6]]


def test_the_page_range_survives_a_restart(start_app, documents_dir, tmp_path):
    before = closing_on(["Page 4 is about the pulp."])
    app = start_app(before)
    job_id = app.upload(five_pages(tmp_path), pages="3-5")["id"]
    app.wait_for_page(job_id, 1)
    app.stop()

    after = FakeTranslator()
    job = start_app(after).wait_for(job_id)

    assert job["state"] == "done"
    assert before.requests + after.requests == [[f"Page {number} is about the pulp."] for number in (3, 4, 5)]
    assert "p. 2 del original" not in pdf_text(documents_dir / "Aldi" / "Pulpa" / "Pulpa (es).pdf")


def test_progress_moves_page_by_page(app, translator, tmp_path):
    seen = []
    translate = translator.translate_texts

    def notes_the_progress(texts, terms):
        (job,) = app.get("/api/jobs")["jobs"]
        seen.append((job["page"], job["pages"]))
        return translate(texts, terms)

    translator.translate_texts = notes_the_progress
    job = app.wait_for(app.upload(five_pages(tmp_path), pages="1-3")["id"])

    assert seen == [(0, 3), (1, 3), (2, 3)]
    assert (job["page"], job["pages"]) == (3, 3)
