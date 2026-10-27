"""Mantener el diseño: the Spanish written over the original pages by `pdf2zh-next`, for pages with text (#13)."""

import json
import urllib.error

import pymupdf
import pytest

from aldi.estimate import estimate
from aldi.gemini import MODEL_CHAIN, GeminiTranslator
from aldi.translator import QuotaExhausted
from conftest import FakeGemini, build_born_digital_pdf, build_mixed_pdf, build_scanned_pdf, quota_error

FLASH = MODEL_CHAIN[0]

NOT_OFFERED = "Mantener el diseño solo se puede elegir si todas las páginas elegidas tienen texto seleccionable."


def three_pages(tmp_path):
    pages = [[f"Page {number} describes the enamel of the first molar."] for number in range(1, 4)]
    return build_born_digital_pdf(tmp_path / "Esmalte.pdf", pages)


def page_texts(path):
    return [" ".join(page.get_text().split()) for page in pymupdf.open(path)]


def refused(call) -> tuple[int, str]:
    with pytest.raises(urllib.error.HTTPError) as error:
        call()
    return error.value.code, json.load(error.value)["error"]


def test_mantener_el_diseno_is_offered_only_when_every_chosen_page_has_text(app, tmp_path):
    text = build_born_digital_pdf(tmp_path / "texto.pdf", [["Page 1"], ["Page 2"]])
    scanned = build_scanned_pdf(tmp_path / "escaneado.pdf", [[]])
    mixed = build_mixed_pdf(tmp_path / "Mixto.pdf", [text, scanned])

    document = app.drop(mixed)

    assert document["layout_estimate"] is None
    chosen = app.get(f"/api/documents/{document['id']}/estimate?pages=1-2")
    assert chosen["layout_estimate"] == estimate([False, False], layout=True)
    assert app.get(f"/api/documents/{document['id']}/estimate?pages=2-3")["layout_estimate"] is None
    start = {"document": document["id"], "pages": "2-3", "output_mode": "layout"}
    assert refused(lambda: app.send("POST", "/api/jobs", start)) == (400, NOT_OFFERED)
    assert app.get("/api/jobs")["jobs"] == []


def test_mantener_el_diseno_writes_only_the_chosen_pages_in_spanish(app, translator, documents_dir, tmp_path):
    document = app.drop(three_pages(tmp_path))

    started = app.send("POST", "/api/jobs", {"document": document["id"], "pages": "1, 3", "output_mode": "layout"})
    job = app.wait_for(started["id"], timeout=180)

    assert job["state"] == "done", job
    assert job["output_mode"] == "layout"
    assert job["results"] == ["Esmalte (es, diseño).pdf"]
    texts = page_texts(documents_dir / "Aldi" / "Esmalte" / "Esmalte (es, diseño).pdf")
    assert len(texts) == 2
    assert "ES: Page 1 describes the enamel" in texts[0]
    assert "ES: Page 3 describes the enamel" in texts[1]
    assert not any("Page 2" in text for text in texts)
    assert translator.layout_prompts


def test_a_quota_pause_in_mantener_el_diseno_waits_and_then_finishes(app, translator, clock, documents_dir, tmp_path):
    answer = translator.answer_layout_prompt
    failures = [QuotaExhausted()]

    def out_of_quota_once(prompt):
        if failures:
            raise failures.pop()
        return answer(prompt)

    translator.answer_layout_prompt = out_of_quota_once
    document = app.drop(three_pages(tmp_path))

    started = app.send("POST", "/api/jobs", {"document": document["id"], "pages": "2", "output_mode": "layout"})
    job = app.wait_for(started["id"], timeout=180)

    assert job["state"] == "done", job
    assert clock.waits == [5 * 60]
    (text,) = page_texts(documents_dir / "Aldi" / "Esmalte" / "Esmalte (es, diseño).pdf")
    assert "ES: Page 2 describes the enamel" in text


def test_mantener_el_diseno_takes_its_own_time_per_page():
    assert estimate([False] * 30, layout=True) != estimate([False] * 30)


def test_mantener_el_diseno_keeps_to_flash_and_waits_out_its_minute_quota(
    start_app, clock, documents_dir, tmp_path
):
    gemini = FakeGemini()
    gemini.fail(FLASH, quota_error("20s"))
    app = start_app(GeminiTranslator(gemini, sleep=clock.sleep, clock=clock.now))
    document = app.drop(three_pages(tmp_path))

    started = app.send("POST", "/api/jobs", {"document": document["id"], "pages": "1", "output_mode": "layout"})
    job = app.wait_for(started["id"], timeout=180)

    assert job["state"] == "done", job
    assert set(gemini.calls) == {FLASH}
    assert clock.waits == [20]
    (text,) = page_texts(documents_dir / "Aldi" / "Esmalte" / "Esmalte (es, diseño).pdf")
    assert "ES: Page 1 describes the enamel" in text
