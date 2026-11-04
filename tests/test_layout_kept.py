"""Mantener el diseño: the Spanish written over the original pages by `pdf2zh-next`, for pages with text (#13)."""

import multiprocessing
import sys
import threading
import time

import psutil
import pymupdf
import pytest
from google.genai import errors

from aldi.estimate import estimate
from aldi.gemini import MODEL_CHAIN, GeminiTranslator
from aldi.layout import write_layout_kept
from aldi.translator import QuotaExhausted
from conftest import (
    FakeGemini,
    build_born_digital_pdf,
    build_mixed_pdf,
    build_scanned_pdf,
    busy_error,
    quota_error,
    refused,
    wait_until,
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

    def out_of_quota_once(prompt, waited=0):
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

    def out_of_quota_once(prompt, waited=0):
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


@pytest.mark.parametrize(("failure", "wait"), [(quota_error("38s"), 38), (busy_error(), 10)], ids=["429", "503"])
def test_mantener_el_diseno_shows_the_quota_pause_while_it_waits_on_gemini(
    start_app, clock, documents_dir, tmp_path, failure, wait
):
    # Bug: the wait for flash's quota or a busy flash went by silently, `Traduciendo…` for 40 minutes.
    gemini = FakeGemini()
    gemini.fail(FLASH, failure)
    app = start_app(GeminiTranslator(gemini, sleep=clock.sleep, clock=clock.now))
    clock.hold()
    document = app.drop(three_pages(tmp_path))
    started = app.send("POST", "/api/jobs", {"document": document["id"], "pages": "1", "output_mode": "layout"})

    paused = app.wait_for(started["id"], until=lambda job: job["state"] == "paused", timeout=180)

    assert paused["notice"] == "En pausa hasta que Gemini vuelva a estar disponible"
    clock.release()
    assert app.wait_for(started["id"], timeout=180)["state"] == "done"
    assert clock.waits == [wait]


def test_a_mantener_el_diseno_job_paused_on_flash_lets_a_reading_version_run_first(
    app, translator, clock, documents_dir, tmp_path
):
    # Bug: a Job paused on flash's daily quota held the queue for 9 hours.
    answer = translator.answer_layout_prompt
    failures = [QuotaExhausted()]
    in_flight, most = [0], [0]

    def one_at_a_time(call):
        def counted(*args):
            in_flight[0] += 1
            most[0] = max(most[0], in_flight[0])
            try:
                time.sleep(0.05)
                return call(*args)
            finally:
                in_flight[0] -= 1

        return counted

    def out_of_quota_once(prompt, waited=0):
        if failures:
            raise failures.pop()
        return answer(prompt)

    translator.answer_layout_prompt = one_at_a_time(out_of_quota_once)
    translator.translate_texts = one_at_a_time(translator.translate_texts)
    clock.hold()
    document = app.drop(three_pages(tmp_path))
    layout = app.send("POST", "/api/jobs", {"document": document["id"], "pages": "1", "output_mode": "layout"})
    app.wait_for(layout["id"], until=lambda job: job["state"] == "paused", timeout=180)
    reading = app.upload(build_born_digital_pdf(tmp_path / "Dentina.pdf", [["The dentin is softer."]]))

    assert app.wait_for(reading["id"])["state"] == "done"
    assert app.get(f"/api/jobs/{layout['id']}")["state"] == "paused"
    clock.release()
    assert app.wait_for(layout["id"], timeout=180)["state"] == "done"
    assert clock.waits == [5 * 60]
    assert most[0] == 1
    assert (documents_dir / "Aldi" / "Esmalte" / "Esmalte (es, diseño).pdf").exists()


def test_a_mantener_el_diseno_job_waits_for_the_paused_one_while_a_reading_version_runs(
    app, translator, clock, tmp_path
):
    # Bug: a paused Job gave its turn to any Job, so a second Mantener el diseño started BabelDOC beside the paused
    # one's (about 2 GB each) only to meet the same exhausted flash quota.
    answer = translator.answer_layout_prompt
    failures = [QuotaExhausted()]

    def out_of_quota_once(prompt, waited=0):
        if failures:
            raise failures.pop()
        return answer(prompt)

    translator.answer_layout_prompt = out_of_quota_once
    clock.hold()
    first = app.drop(three_pages(tmp_path))
    first = app.send("POST", "/api/jobs", {"document": first["id"], "pages": "1", "output_mode": "layout"})
    app.wait_for(first["id"], until=lambda job: job["state"] == "paused", timeout=180)
    pages = [["The dentin lies under the enamel of the molar."]]
    second = app.drop(build_born_digital_pdf(tmp_path / "Dentina.pdf", pages))
    second = app.send("POST", "/api/jobs", {"document": second["id"], "pages": "1", "output_mode": "layout"})
    reading = app.upload(build_born_digital_pdf(tmp_path / "Pulpa.pdf", [["The pulp is soft."]]))

    assert app.wait_for(reading["id"])["state"] == "done"
    assert app.get(f"/api/jobs/{second['id']}")["state"] == "queued"
    assert not any("dentin" in prompt for prompt in translator.layout_prompts)
    clock.release()
    assert app.wait_for(first["id"], timeout=180)["state"] == "done"
    assert app.wait_for(second["id"], timeout=180)["state"] == "done"
    dentin = [index for index, prompt in enumerate(translator.layout_prompts) if "dentin" in prompt]
    enamel = [index for index, prompt in enumerate(translator.layout_prompts) if "enamel of the first" in prompt]
    assert dentin and enamel and max(enamel) < min(dentin)


def test_each_mantener_el_diseno_job_counts_its_own_waits_on_flash(start_app, clock, tmp_path):
    # Bug: the count of waits in a row lived in the translator, so a Job that failed after three waits left the next
    # Job's first 429 a full five-minute Quota Pause instead of the 429's own delay.
    gemini = FakeGemini()
    invalid = errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
    gemini.fail(FLASH, *[quota_error("20s")] * 3, invalid, quota_error("20s"))
    app = start_app(GeminiTranslator(gemini, sleep=clock.sleep, clock=clock.now))
    source = three_pages(tmp_path)

    for expected in ("failed", "done"):
        document = app.drop(source)
        started = app.send("POST", "/api/jobs", {"document": document["id"], "pages": "1", "output_mode": "layout"})
        assert app.wait_for(started["id"], timeout=180)["state"] == expected

    assert clock.waits == [20, 20, 20, 20]


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


def test_cancelling_mantener_el_diseno_ends_babeldoc_and_drops_the_job(app, translator, documents_dir, tmp_path):
    document = app.drop(three_pages(tmp_path))
    started = app.send("POST", "/api/jobs", {"document": document["id"], "pages": "", "output_mode": "layout"})
    folder = tmp_path / "data" / "jobs" / started["id"]
    wait_until(lambda: (folder / "layout" / "work").exists(), timeout=120)

    cancelling = time.monotonic()
    app.send("POST", f"/api/jobs/{started['id']}/cancel")

    wait_until(lambda: not folder.exists(), timeout=10)
    assert time.monotonic() - cancelling < 5
    assert not [child for child in multiprocessing.active_children() if child.name == "aldi-babeldoc"]
    assert app.get("/api/jobs")["jobs"] == []
    assert translator.layout_prompts == []
    assert not documents_dir.exists()


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_an_answer_that_fails_after_she_cancelled_ends_the_job_quietly(app, translator, github, tmp_path):
    # Bug: the error handler looked up the cancelled Job, and its thread crashed instead of stopping.
    asked, cancelled = threading.Event(), threading.Event()

    def fails_once_cancelled(prompt, waited=0):
        asked.set()
        cancelled.wait(timeout=60)
        raise RuntimeError("the request ended with the Job")

    translator.answer_layout_prompt = fails_once_cancelled
    document = app.drop(three_pages(tmp_path))
    started = app.send("POST", "/api/jobs", {"document": document["id"], "pages": "1", "output_mode": "layout"})
    assert asked.wait(timeout=180)

    app.send("POST", f"/api/jobs/{started['id']}/cancel")
    cancelled.set()

    wait_until(lambda: not (tmp_path / "data" / "jobs" / started["id"]).exists(), timeout=10)
    assert github.reports() == []


def test_any_other_gemini_error_in_mantener_el_diseno_fails_the_job_and_reports_it(
    app, translator, github, documents_dir, tmp_path
):
    answer = translator.answer_layout_prompt
    failures = [RuntimeError("Gemini answered something unreadable")]

    def unreadable_once(prompt, waited=0):
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


def lay_out_in_an_app(source, folder, pids):
    """The app's process for the test below: Mantener el diseño whose answers never come; sends BabelDOC's pid."""

    def send_child_pid():
        while not (children := [c for c in multiprocessing.active_children() if c.name == "aldi-babeldoc"]):
            time.sleep(0.05)
        pids.put(children[0].pid)

    threading.Thread(target=send_child_pid, daemon=True).start()
    write_layout_kept(source, range(1, 11), folder, lambda _prompt: threading.Event().wait(), lambda: False)


def test_babeldocs_process_ends_when_the_app_is_killed(tmp_path):
    # Bug: a hard kill of the app left BabelDOC's process laying out, its memory held, with nobody to answer it.
    (folder := tmp_path / "layout").mkdir()
    context = multiprocessing.get_context("spawn")
    pids = context.Queue()
    pages = [[f"Page {number} describes the enamel of the first molar."] for number in range(1, 11)]
    source = build_born_digital_pdf(tmp_path / "Esmalte.pdf", pages)
    app = context.Process(target=lay_out_in_an_app, args=(source, folder, pids))
    app.start()
    babeldoc = psutil.Process(pids.get(timeout=60))
    # Started: BabelDOC is at work, not reading the app's pipe.
    wait_until(lambda: (folder / "work").exists(), timeout=120)

    app.kill()
    app.join()

    # Laying out ten pages takes BabelDOC far longer than this.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if babeldoc.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.1)
    else:
        babeldoc.kill()
        raise AssertionError("BabelDOC's process outlived the app")
