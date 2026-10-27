"""Jobs survive closing the app, wait their turn in a queue, and stay listed afterwards (#8)."""

import json
import threading
import urllib.error
from pathlib import Path

import pytest

from aldi.translator import QuotaExhausted
from conftest import AppClosed, FakeTranslator, build_born_digital_pdf, pdf_text


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
