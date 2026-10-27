"""Jobs survive closing the app, wait their turn in a queue, and stay listed afterwards (#8)."""

import json
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from aldi.translator import KeyRefused, QuotaExhausted
from conftest import AppClosed, FakeTranslator, build_born_digital_pdf, pdf_text, refused, wait_until


def closing_on(texts_at_close):
    """A translator whose app closes when it is asked for `texts_at_close`: from then on it translates nothing."""
    translator = FakeTranslator()
    translate = translator.translate_texts
    closed = False

    def translate_until_closed(texts, terms):
        nonlocal closed
        closed = closed or list(texts) == texts_at_close
        if closed:
            raise AppClosed
        return translate(texts, terms)

    translator.translate_texts = translate_until_closed
    return translator


def test_a_job_resumes_after_a_restart_with_every_page_exactly_once(start_app, documents_dir, tmp_path):
    pages = [[f"Page {number} is about the pulp."] for number in range(1, 6)]
    source = build_born_digital_pdf(tmp_path / "Pulpa.pdf", pages)
    before = closing_on(["Page 4 is about the pulp."])
    app = start_app(before)
    job_id = app.upload(source)["id"]
    app.wait_for_page(job_id, 3)
    app.stop()

    after = FakeTranslator()
    job = start_app(after).wait_for(job_id)

    assert job["state"] == "done"
    assert before.requests + after.requests == [[f"Page {number} is about the pulp."] for number in range(1, 6)]
    text = pdf_text(documents_dir / "Aldi" / "Pulpa" / "Pulpa (es).pdf")
    for number in range(1, 6):
        assert text.count(f"p. {number} del original") == 1
        assert text.count(f"ES: Page {number} is about the pulp.") == 1
    assert text.index("p. 3 del original") < text.index("p. 4 del original")


def test_a_second_document_waits_until_the_first_is_done(app, translator, tmp_path):
    first = build_born_digital_pdf(tmp_path / "Esmalte.pdf", [["Enamel one."], ["Enamel two."]])
    second = build_born_digital_pdf(tmp_path / "Dentina.pdf", [["Dentin one."], ["Dentin two."]])
    release = threading.Event()
    translate = translator.translate_texts

    def waits_on_the_first_page(texts, terms):
        if texts == ["Enamel one."]:
            release.wait(timeout=30)
        return translate(texts, terms)

    translator.translate_texts = waits_on_the_first_page
    first_id = app.upload(first)["id"]
    second_id = app.upload(second)["id"]

    assert app.get(f"/api/jobs/{second_id}")["state"] == "queued"
    release.set()
    assert app.wait_for(first_id)["state"] == "done"
    assert app.wait_for(second_id)["state"] == "done"
    assert translator.requests == [["Enamel one."], ["Enamel two."], ["Dentin one."], ["Dentin two."]]


def test_queued_jobs_run_in_the_order_dropped_after_a_restart(start_app, tmp_path):
    names = ["Esmalte", "Dentina", "Pulpa"]
    sources = [build_born_digital_pdf(tmp_path / f"{name}.pdf", [[f"{name} one."], [f"{name} two."]]) for name in names]
    before = closing_on(["Esmalte two."])
    app = start_app(before)
    ids = [app.upload(source)["id"] for source in sources]
    app.wait_for_page(ids[0], 1)
    app.stop()

    after = FakeTranslator()
    app = start_app(after)
    jobs = [app.wait_for(job_id) for job_id in ids]

    assert [job["state"] for job in jobs] == ["done"] * 3
    assert before.requests + after.requests == [[f"{name} {n}."] for name in names for n in ("one", "two")]


def test_the_results_folder_opens_when_a_job_finishes(app, opened, documents_dir, tmp_path):
    source = build_born_digital_pdf(tmp_path / "Corrêa.pdf", [["The crown."]])

    app.wait_for(app.upload(source)["id"])

    assert opened == [documents_dir / "Aldi" / "Corrêa"]


def test_past_jobs_are_listed_after_a_restart_and_open_their_folder(
    start_app, translator, opened, documents_dir, tmp_path
):
    app = start_app(translator)
    for name in ("Visão geral", "Corrêa"):
        source = build_born_digital_pdf(tmp_path / f"{name}.pdf", [[f"{name} text."]])
        app.wait_for(app.upload(source)["id"])
    app.stop()
    opened.clear()

    app = start_app(FakeTranslator())
    jobs = app.get("/api/jobs")["jobs"]

    assert [(job["name"], job["state"], job["results"]) for job in jobs] == [
        ("Visão geral", "done", ["Visão geral (es).pdf", "Visão geral (es).docx"]),
        ("Corrêa", "done", ["Corrêa (es).pdf", "Corrêa (es).docx"]),
    ]
    assert app.send("POST", f"/api/jobs/{jobs[0]['id']}/open-folder") == {}
    assert opened == [documents_dir / "Aldi" / "Visão geral"]


def test_opening_the_folder_of_an_unknown_job_is_refused(app, opened):
    with pytest.raises(urllib.error.HTTPError) as refused:
        app.send("POST", "/api/jobs/nada/open-folder")

    assert refused.value.code == 404
    assert opened == []


def test_a_folder_that_cannot_be_opened_leaves_the_job_done(start_app, translator, tmp_path):
    def no_file_browser(folder):
        raise FileNotFoundError("xdg-open")

    app = start_app(translator, open_folder=no_file_browser)
    source = build_born_digital_pdf(tmp_path / "Raíz.pdf", [["The root."]])

    job = app.wait_for(app.upload(source)["id"])

    assert job["state"] == "done"
    with pytest.raises(urllib.error.HTTPError) as refused:
        app.send("POST", f"/api/jobs/{job['id']}/open-folder")
    assert refused.value.code == 500
    assert json.load(refused.value)["error"] == "No se pudo abrir la carpeta."


def test_a_long_document_name_keeps_her_results_within_windows_paths(app, documents_dir, tmp_path):
    long_name = "Tratado de periodoncia clínica e implantología oral " * 6 + ".pdf"
    source = build_born_digital_pdf(tmp_path / "source.pdf", [["The gum."]])

    first = app.wait_for(app.upload(source, filename=long_name)["id"])
    second = app.wait_for(app.upload(source, filename=long_name)["id"])

    delivered = [Path(job["folder"]) / result for job in (first, second) for result in job["results"]]
    assert len(delivered) == 4
    for path in delivered:
        assert path.is_file()
        # Leaves 60 characters for her Documents folder (`C:\Users\<nombre>\OneDrive\Documentos`) under 260.
        assert len(str(path.relative_to(documents_dir))) < 200


def test_a_job_closed_during_a_quota_pause_resumes_after_a_restart(start_app, documents_dir, tmp_path):
    pages = [[f"Page {number} is about the pulp."] for number in range(1, 4)]
    source = build_born_digital_pdf(tmp_path / "Pulpa.pdf", pages)
    before = FakeTranslator()
    translate = before.translate_texts

    def out_of_quota_after_the_first_page(texts, terms):
        if texts != ["Page 1 is about the pulp."]:
            raise QuotaExhausted
        return translate(texts, terms)

    before.translate_texts = out_of_quota_after_the_first_page
    app = start_app(before)
    job_id = app.upload(source)["id"]
    app.wait_for(job_id, until=lambda job: job["state"] == "paused")
    app.stop()

    after = FakeTranslator()
    job = start_app(after).wait_for(job_id)

    assert job["state"] == "done"
    assert after.requests == [["Page 2 is about the pulp."], ["Page 3 is about the pulp."]]
    text = pdf_text(documents_dir / "Aldi" / "Pulpa" / "Pulpa (es).pdf")
    assert [text.count(f"ES: Page {number} is about the pulp.") for number in range(1, 4)] == [1, 1, 1]


def blocking_on(translator, text):
    """Holds the translator's request for `text` until the returned event is set."""
    release = threading.Event()
    translate = translator.translate_texts

    def waits(texts, terms):
        if text in texts:
            release.wait(timeout=30)
        return translate(texts, terms)

    translator.translate_texts = waits
    return release


def test_a_job_paused_by_a_refused_key_lets_the_next_one_run_and_finishes_after_it(app, translator, tmp_path):
    # Bug: a paused Job held the queue, so a document dropped after it waited for hours on someone else's quota.
    first = build_born_digital_pdf(tmp_path / "Esmalte.pdf", [["Enamel one."], ["Enamel two."]])
    second = build_born_digital_pdf(tmp_path / "Dentina.pdf", [["Dentin one."]])
    translate = translator.translate_texts
    refusals = [KeyRefused()]
    in_flight, most = [0], [0]

    def counted(texts, terms):
        if texts == ["Enamel two."] and refusals:
            raise refusals.pop()
        in_flight[0] += 1
        most[0] = max(most[0], in_flight[0])
        try:
            time.sleep(0.05)
            return translate(texts, terms)
        finally:
            in_flight[0] -= 1

    translator.translate_texts = counted
    first_id = app.upload(first)["id"]
    app.wait_for(first_id, until=lambda job: job["state"] == "paused")
    second_id = app.upload(second)["id"]

    assert app.wait_for(second_id)["state"] == "done"
    assert app.get(f"/api/jobs/{first_id}")["state"] == "paused"
    app.send("PUT", "/api/settings", {"key": "AIza-valida-2"})
    assert app.wait_for(first_id)["state"] == "done"
    assert translator.requests == [["Enamel one."], ["Dentin one."], ["Enamel two."]]
    assert most[0] == 1


def test_a_paused_job_waits_for_the_running_one_before_it_goes_on(app, translator, tmp_path):
    first = build_born_digital_pdf(tmp_path / "Esmalte.pdf", [["Enamel one."], ["Enamel two."]])
    second = build_born_digital_pdf(tmp_path / "Dentina.pdf", [["Dentin one."], ["Dentin two."]])
    release = blocking_on(translator, "Dentin one.")
    translate = translator.translate_texts
    refusals = [KeyRefused()]

    def refused_once(texts, terms):
        if texts == ["Enamel two."] and refusals:
            raise refusals.pop()
        return translate(texts, terms)

    translator.translate_texts = refused_once
    first_id = app.upload(first)["id"]
    app.wait_for(first_id, until=lambda job: job["state"] == "paused")
    second_id = app.upload(second)["id"]
    app.wait_for(second_id, until=lambda job: job["state"] == "running")

    app.send("PUT", "/api/settings", {"key": "AIza-valida-2"})
    waiting = app.wait_for(first_id, until=lambda job: job["state"] != "paused")

    assert (waiting["state"], waiting["notice"]) == ("queued", "")
    release.set()
    assert app.wait_for(second_id)["state"] == "done"
    assert app.wait_for(first_id)["state"] == "done"
    assert translator.requests == [["Enamel one."], ["Dentin one."], ["Dentin two."], ["Enamel two."]]


def job_folder(tmp_path, job_id):
    return tmp_path / "data" / "jobs" / job_id


def test_cancelling_a_queued_job_drops_it_and_its_folder(app, translator, tmp_path):
    first = build_born_digital_pdf(tmp_path / "Esmalte.pdf", [["Enamel one."]])
    second = build_born_digital_pdf(tmp_path / "Dentina.pdf", [["Dentin one."]])
    release = blocking_on(translator, "Enamel one.")
    first_id = app.upload(first)["id"]
    second_id = app.upload(second)["id"]

    assert app.send("POST", f"/api/jobs/{second_id}/cancel") == {}

    assert [job["id"] for job in app.get("/api/jobs")["jobs"]] == [first_id]
    wait_until(lambda: not job_folder(tmp_path, second_id).exists())
    release.set()
    assert app.wait_for(first_id)["state"] == "done"
    assert translator.requests == [["Enamel one."]]


def test_cancelling_the_running_job_stops_it_and_the_next_one_runs(app, translator, documents_dir, tmp_path):
    first = build_born_digital_pdf(tmp_path / "Esmalte.pdf", [["Enamel one."], ["Enamel two."]])
    second = build_born_digital_pdf(tmp_path / "Dentina.pdf", [["Dentin one."]])
    release = blocking_on(translator, "Enamel one.")
    first_id = app.upload(first)["id"]
    second_id = app.upload(second)["id"]
    app.wait_for(first_id, until=lambda job: job["state"] == "running")

    app.send("POST", f"/api/jobs/{first_id}/cancel")
    release.set()

    assert app.wait_for(second_id)["state"] == "done"
    assert [job["id"] for job in app.get("/api/jobs")["jobs"]] == [second_id]
    wait_until(lambda: not job_folder(tmp_path, first_id).exists())
    assert ["Enamel two."] not in translator.requests
    assert not (documents_dir / "Aldi" / "Esmalte").exists()


def test_cancelling_a_paused_job_drops_it_and_its_folder(app, translator, tmp_path):
    translator.translate_texts = lambda texts, terms: (_ for _ in ()).throw(KeyRefused())
    job_id = app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", [["Enamel one."]]))["id"]
    app.wait_for(job_id, until=lambda job: job["state"] == "paused")

    app.send("POST", f"/api/jobs/{job_id}/cancel")

    assert app.get("/api/jobs")["jobs"] == []
    wait_until(lambda: not job_folder(tmp_path, job_id).exists())


def test_a_cancelled_job_stays_gone_after_a_restart(start_app, tmp_path):
    translator = FakeTranslator()
    app = start_app(translator)
    release = blocking_on(translator, "Enamel one.")
    job_id = app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", [["Enamel one."]]))["id"]
    app.wait_for(job_id, until=lambda job: job["state"] == "running")
    app.send("POST", f"/api/jobs/{job_id}/cancel")
    release.set()
    app.stop()

    assert start_app(FakeTranslator()).get("/api/jobs")["jobs"] == []
    assert not job_folder(tmp_path, job_id).exists()


def test_cancelling_a_retry_keeps_her_first_results(app, translator, documents_dir, tmp_path):
    translate = translator.translate_texts
    failing = [True]

    def fails_page_two(texts, terms):
        if texts == ["Enamel two."] and failing[0]:
            raise RuntimeError("unreadable")
        return translate(texts, terms)

    translator.translate_texts = fails_page_two
    source = build_born_digital_pdf(tmp_path / "Esmalte.pdf", [["Enamel one."], ["Enamel two."]])
    job_id = app.upload(source)["id"]
    assert app.wait_for(job_id)["failed_pages"] == [2]
    failing[0] = False
    release = blocking_on(translator, "Enamel two.")
    app.send("POST", f"/api/jobs/{job_id}/retry")
    app.wait_for(job_id, until=lambda job: job["state"] == "running")

    app.send("POST", f"/api/jobs/{job_id}/cancel")
    release.set()

    wait_until(lambda: not job_folder(tmp_path, job_id).exists())
    results = documents_dir / "Aldi" / "Esmalte"
    assert sorted(path.name for path in results.iterdir()) == ["Esmalte (es).docx", "Esmalte (es).pdf"]


def test_a_finished_or_unknown_job_cannot_be_cancelled(app, tmp_path):
    job_id = app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", [["Enamel one."]]))["id"]
    app.wait_for(job_id)

    assert refused(lambda: app.send("POST", f"/api/jobs/{job_id}/cancel")) == (
        409,
        "Esta traducción ya terminó.",
    )
    assert refused(lambda: app.send("POST", "/api/jobs/nada/cancel"))[0] == 404
    assert app.get(f"/api/jobs/{job_id}")["state"] == "done"
