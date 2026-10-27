"""Mantener el diseño: the Spanish written over the original pages by `pdf2zh-next`, for pages with text (#13)."""

import sys
import threading
import time

import pymupdf

from aldi.estimate import estimate
from aldi.gemini import MODEL_CHAIN, GeminiTranslator
from aldi.translator import QuotaExhausted
from conftest import (
    FakeGemini,
    build_born_digital_pdf,
    build_mixed_pdf,
    build_scanned_pdf,
    quota_error,
    refused,
)

FLASH = MODEL_CHAIN[0]

NOT_OFFERED = "Mantener el diseño solo se puede elegir si todas las páginas elegidas tienen texto seleccionable."


def three_pages(tmp_path):
    pages = [[f"Page {number} describes the enamel of the first molar."] for number in range(1, 4)]
    return build_born_digital_pdf(tmp_path / "Esmalte.pdf", pages)


def page_texts(path):
    # BabelDOC's fonts give its spaces no Unicode: they read back as U+0003.
    return [" ".join(page.get_text().replace("\x03", " ").split()) for page in pymupdf.open(path)]


def test_mantener_el_diseno_is_offered_only_when_every_chosen_page_has_text(app, tmp_path):
    text = build_born_digital_pdf(tmp_path / "texto.pdf", [["Page 1"], ["Page 2"]])
    scanned = build_scanned_pdf(tmp_path / "escaneado.pdf", [[]])
    mixed = build_mixed_pdf(tmp_path / "Mixto.pdf", [text, scanned])

    document = app.drop(mixed)

    assert document["layout_estimate"] is None
    chosen = app.get(f"/api/documents/{document['id']}/estimate?pages=1-2")
    assert chosen["layout_estimate"] == "Tiempo estimado: un minuto"
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
    # Bug: BabelDOC ran in the app's own process and kept 1-2 GB there after every run.
    assert not [name for name in sys.modules if name.startswith("babeldoc")]


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


def test_a_resumed_mantener_el_diseno_run_drops_the_pause_notice_as_it_starts_again(app, translator, clock, tmp_path):
    # Bug: the run is one request, so the notice stayed up until the whole resumed run returned.
    answer = translator.answer_layout_prompt
    failures = [QuotaExhausted()]
    answering = threading.Event()

    def out_of_quota_once(prompt):
        if failures:
            raise failures.pop()
        answering.wait(timeout=60)
        return answer(prompt)

    translator.answer_layout_prompt = out_of_quota_once
    clock.hold()
    document = app.drop(three_pages(tmp_path))
    started = app.send("POST", "/api/jobs", {"document": document["id"], "pages": "2", "output_mode": "layout"})
    app.wait_for(started["id"], until=lambda job: job["state"] == "paused", timeout=180)

    clock.release()
    job = app.wait_for(started["id"], until=lambda job: job["state"] != "paused")

    assert (job["state"], job["notice"]) == ("running", "")
    answering.set()
    assert app.wait_for(started["id"], timeout=180)["state"] == "done"


def test_mantener_el_diseno_takes_its_own_time_per_page():
    assert estimate([False] * 30, layout=True) != estimate([False] * 30)


def test_mantener_el_diseno_keeps_to_flash_and_waits_out_its_minute_quota(start_app, clock, documents_dir, tmp_path):
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


def test_closing_the_app_while_mantener_el_diseno_lays_out_stops_it_at_once(start_app, translator, tmp_path):
    app = start_app(translator)
    document = app.drop(three_pages(tmp_path))
    started = app.send("POST", "/api/jobs", {"document": document["id"], "pages": "", "output_mode": "layout"})
    app.wait_for(started["id"], until=lambda job: job["state"] == "running")

    closing = time.monotonic()
    app.stop()

    assert time.monotonic() - closing < 3
    assert translator.layout_prompts == []
    job = start_app(translator).wait_for(started["id"], timeout=180)
    assert job["state"] == "done", job


def test_any_other_gemini_error_in_mantener_el_diseno_fails_the_job_and_reports_it(
    app, translator, github, documents_dir, tmp_path
):
    answer = translator.answer_layout_prompt
    failures = [RuntimeError("Gemini answered something unreadable")]

    def unreadable_once(prompt):
        if failures:
            raise failures.pop()
        return answer(prompt)

    translator.answer_layout_prompt = unreadable_once
    document = app.drop(three_pages(tmp_path))

    started = app.send("POST", "/api/jobs", {"document": document["id"], "pages": "2", "output_mode": "layout"})
    job = app.wait_for(started["id"], timeout=180)

    assert job["state"] == "failed", job
    assert job["results"] == []
    assert not (documents_dir / "Aldi" / "Esmalte" / "Esmalte (es, diseño).pdf").exists()
    [report] = github.reports()
    assert "Output Mode: Layout-kept Translation" in report["body"]
