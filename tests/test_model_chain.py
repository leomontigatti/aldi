"""Gemini busy, out of quota or out of reach: the Job moves down the Model Chain, pauses and resumes (#7)."""

import threading
import time

import pymupdf
import pytest
from google.genai import errors

from aldi.gemini import BUSY_WAITS, MODEL_CHAIN, GeminiTranslator
from aldi.jobs import JobStore
from aldi.reports import Reporter
from aldi.translator import QuotaExhausted
from conftest import (
    FakeGemini,
    FakeGitHub,
    FakeTranslator,
    build_born_digital_pdf,
    build_scanned_pdf,
    busy_error,
    offline_error,
    quota_error,
)

FLASH, LITE, GEMMA = MODEL_CHAIN
PAGES = [["The enamel is hard."], ["The dentin is softer."], ["The pulp is alive."]]


@pytest.fixture
def gemini():
    return FakeGemini()


@pytest.fixture
def translator(gemini, clock):
    """The real translator, with the fake behind it in place of Gemini's client."""
    return GeminiTranslator(gemini, sleep=clock.sleep, clock=clock.now)


def spanish_text(documents_dir, name):
    pdf = pymupdf.open(documents_dir / "Aldi" / name / f"{name} (es).pdf")
    return " ".join("".join(page.get_text() for page in pdf).split())


def assert_every_page_once_in_order(documents_dir, name):
    text = spanish_text(documents_dir, name)
    spanish = [f"ES: {paragraphs[0]}" for paragraphs in PAGES]
    assert [text.count(page) for page in spanish] == [1, 1, 1]
    assert text.index(spanish[0]) < text.index(spanish[1]) < text.index(spanish[2])


def test_a_model_out_of_quota_hands_the_job_to_the_next(app, gemini, clock, documents_dir, tmp_path):
    gemini.fail(FLASH, quota_error())

    job = app.wait_for(app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES))["id"])

    assert job["state"] == "done"
    assert job["notice"] == ""
    assert gemini.calls == [FLASH, LITE, LITE, LITE]
    assert clock.waits == []
    assert_every_page_once_in_order(documents_dir, "Esmalte")


def test_a_model_out_of_quota_takes_the_job_back_once_the_429s_retry_delay_is_over(translator, gemini, clock):
    gemini.fail(FLASH, quota_error(retry_delay="30s"))

    for wait in (0, 20, 15):
        clock.advance(wait)
        translator.translate_texts(["The enamel is hard."], {})

    assert gemini.calls == [FLASH, LITE, LITE, FLASH]


def test_the_last_model_gemma_translates_when_both_gemini_models_are_out_of_quota(app, gemini, documents_dir, tmp_path):
    gemini.fail(FLASH, quota_error())
    gemini.fail(LITE, quota_error())

    job = app.wait_for(app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES))["id"])

    assert job["state"] == "done"
    assert job["failed_pages"] == []
    assert gemini.calls == [FLASH, LITE, GEMMA, GEMMA, GEMMA]
    assert_every_page_once_in_order(documents_dir, "Esmalte")


def test_a_busy_model_is_asked_again_only_after_waiting_longer_each_time(app, gemini, clock, documents_dir, tmp_path):
    gemini.fail(FLASH, busy_error(), busy_error())

    job = app.wait_for(app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES))["id"])

    assert job["state"] == "done"
    assert gemini.calls == [FLASH] * 5
    assert clock.waits == list(BUSY_WAITS[:2])
    assert clock.waits[0] >= 10 and clock.waits[1] > clock.waits[0]
    assert_every_page_once_in_order(documents_dir, "Esmalte")


def test_a_model_that_stays_busy_passes_the_page_to_the_next_and_keeps_its_place(app, gemini, clock, tmp_path):
    gemini.fail(FLASH, *[busy_error() for _ in range(len(BUSY_WAITS) + 1)])

    job = app.wait_for(app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES))["id"])

    assert job["state"] == "done"
    assert gemini.calls == [FLASH] * (len(BUSY_WAITS) + 1) + [LITE, FLASH, FLASH]
    assert clock.waits == list(BUSY_WAITS)


def test_quota_out_on_every_model_pauses_the_job_and_it_resumes_where_it_stopped(
    app, gemini, clock, documents_dir, tmp_path
):
    gemini.fail(FLASH, quota_error(), after=1)
    gemini.fail(LITE, quota_error())
    gemini.fail(GEMMA, quota_error())
    clock.hold()

    job_id = app.upload(build_born_digital_pdf(tmp_path / "Dentina.pdf", PAGES))["id"]
    paused = app.wait_for(job_id, until=lambda job: job["state"] == "paused")

    assert paused["notice"] == "En pausa hasta que Gemini vuelva a estar disponible"
    assert paused["page"] == 1
    clock.release()
    job = app.wait_for(job_id)
    assert job["state"] == "done"
    assert job["notice"] == ""
    assert gemini.calls == [FLASH, FLASH, LITE, GEMMA, FLASH, FLASH]
    assert len(clock.waits) == 1 and clock.waits[0] >= 60
    assert_every_page_once_in_order(documents_dir, "Dentina")


def test_offline_says_so_and_the_job_continues_when_the_connection_returns(app, gemini, clock, documents_dir, tmp_path):
    gemini.fail(FLASH, offline_error(), after=1)
    clock.hold()

    job_id = app.upload(build_born_digital_pdf(tmp_path / "Pulpa.pdf", PAGES))["id"]
    paused = app.wait_for(job_id, until=lambda job: job["state"] == "paused")

    assert paused["notice"] == "Sin conexión a internet"
    assert paused["page"] == 1
    clock.release()
    job = app.wait_for(job_id)
    assert job["state"] == "done"
    assert job["notice"] == ""
    assert gemini.calls == [FLASH] * 4
    assert_every_page_once_in_order(documents_dir, "Pulpa")


def test_another_gemini_error_fails_the_page_without_trying_the_other_models(app, gemini, tmp_path):
    invalid = errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
    gemini.fail(FLASH, invalid, invalid, invalid)

    job = app.wait_for(app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES))["id"])

    assert job["state"] == "done"
    assert job["failed_pages"] == [1]
    assert gemini.calls == [FLASH] * 5


def test_a_page_batch_moves_down_the_chain_to_gemma_when_both_gemini_models_are_out_of_quota(
    app, gemini, documents_dir, tmp_path
):
    gemini.fail(FLASH, quota_error())
    gemini.fail(LITE, quota_error())

    job = app.wait_for(app.upload(build_scanned_pdf(tmp_path / "Encia.pdf", [[]] * 5))["id"])

    assert job["state"] == "done"
    assert job["failed_pages"] == []
    assert gemini.calls == [FLASH, LITE, GEMMA, GEMMA]
    assert [spanish_text(documents_dir, "Encia").count(f"ES: Page {n}") for n in range(1, 6)] == [1] * 5


def test_quota_out_on_every_model_pauses_a_scanned_job_and_no_page_fails(app, gemini, clock, documents_dir, tmp_path):
    gemini.fail(FLASH, quota_error(), after=1)
    gemini.fail(LITE, quota_error())
    gemini.fail(GEMMA, quota_error())
    clock.hold()

    job_id = app.upload(build_scanned_pdf(tmp_path / "Encia.pdf", [[]] * 5))["id"]
    paused = app.wait_for(job_id, until=lambda job: job["state"] == "paused")

    assert paused["notice"] == "En pausa hasta que Gemini vuelva a estar disponible"
    assert paused["page"] == 4
    clock.release()
    job = app.wait_for(job_id)
    assert job["state"] == "done"
    assert job["failed_pages"] == []
    assert gemini.calls == [FLASH, FLASH, LITE, GEMMA, FLASH]
    assert [spanish_text(documents_dir, "Encia").count(f"ES: Page {n}") for n in range(1, 6)] == [1] * 5


def test_closing_the_app_during_a_quota_pause_stops_the_job_where_it_was(tmp_path):
    translator = FakeTranslator()

    def out_of_quota(texts, terms):
        raise QuotaExhausted

    translator.translate_texts = out_of_quota
    reporter = Reporter(tmp_path / "data", token=str, make_github=lambda token: FakeGitHub())
    store = JobStore(tmp_path / "data", tmp_path / "Documentos", translator, lambda folder: None, reporter)
    dropped = store.drop("Esmalte.pdf", build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES).read_bytes())
    job = store.start(dropped.id, "")
    deadline = time.monotonic() + 10
    while store.get(job.id).state != "paused" and time.monotonic() < deadline:
        time.sleep(0.02)

    store.close()

    deadline = time.monotonic() + 5
    while any(thread.name.startswith("aldi-job") for thread in threading.enumerate()):
        assert time.monotonic() < deadline, "the Job kept waiting after the app closed"
        time.sleep(0.02)
    reporter.close()
    assert store.get(job.id).state == "paused"
    assert not (tmp_path / "data" / "jobs" / job.id / "error.txt").exists()
