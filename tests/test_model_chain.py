"""Gemini busy, out of quota or out of reach: the Job moves down the Model Chain, pauses and resumes (#7)."""

import json
import socket
import threading
import time

import httpx
import pymupdf
import pytest
from google.genai import errors

from aldi.gemini import MODEL_CHAIN, GeminiTranslator, make_client
from aldi.jobs import JobStore
from aldi.reports import Reporter
from aldi.translator import Offline, QuotaExhausted
from conftest import (
    VALID_KEY,
    FakeGemini,
    FakeGitHub,
    FakeTranslator,
    build_born_digital_pdf,
    build_scanned_pdf,
    busy_error,
    key_error,
    offline_error,
    quota_error,
    wait_until,
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
    assert clock.waits == [10, 30]
    assert_every_page_once_in_order(documents_dir, "Esmalte")


@pytest.mark.parametrize(
    "failures",
    [
        [busy_error(500), busy_error(504)],
        [httpx.ReadTimeout("timed out"), httpx.WriteTimeout("timed out")],
        [httpx.ReadError("connection reset"), httpx.RemoteProtocolError("peer closed connection")],
    ],
    ids=["500 and 504", "timeouts", "dropped connection"],
)
def test_a_server_error_or_a_cut_request_is_waited_out_like_a_busy_model(app, gemini, clock, tmp_path, failures):
    gemini.fail(FLASH, *failures)

    job = app.wait_for(app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES))["id"])

    assert job["state"] == "done"
    assert job["failed_pages"] == []
    assert job["notice"] == ""
    assert gemini.calls == [FLASH] * 5
    assert clock.waits == [10, 30]


def test_a_model_that_stays_busy_passes_the_page_to_the_next_and_keeps_its_place(app, gemini, clock, tmp_path):
    gemini.fail(FLASH, *[busy_error() for _ in range(4)])

    job = app.wait_for(app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES))["id"])

    assert job["state"] == "done"
    assert gemini.calls == [FLASH] * 4 + [LITE, FLASH, FLASH]
    assert clock.waits == [10, 30, 90]


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


def test_a_connection_that_never_opens_is_offline_not_a_busy_model(app, gemini, clock, tmp_path):
    # Bug: a connect timeout was waited out like a busy model, 5 minutes an attempt, before the Job said anything.
    gemini.fail(FLASH, httpx.ConnectTimeout("timed out"), after=1)
    clock.hold()

    job_id = app.upload(build_born_digital_pdf(tmp_path / "Pulpa.pdf", PAGES))["id"]
    paused = app.wait_for(job_id, until=lambda job: job["state"] == "paused")

    assert paused["notice"] == "Sin conexión a internet"
    clock.release()
    assert app.wait_for(job_id)["state"] == "done"
    assert gemini.calls == [FLASH] * 4


def test_the_real_client_reports_a_connection_it_cannot_open_as_offline(monkeypatch):
    # Nothing listens on the port once its socket is closed: connecting is refused at once, no network involved.
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", f"http://127.0.0.1:{port}")
    # A developer's own proxy would otherwise take the request somewhere other than that closed port.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(Offline):
        GeminiTranslator(make_client("AIza-de-prueba")).translate_texts(["The enamel is hard."], {})


@pytest.mark.parametrize("scheme", ["http://", ""])
def test_the_real_client_goes_through_the_system_proxy(monkeypatch, scheme):
    # Bug: the transport that gave connecting its own timeout made httpx ignore the system proxy, so a laptop that
    # reaches the internet only through one read as offline; and a proxy written without `http://` crashed.
    # Gemini's address never resolves: going around the proxy reaches nothing, not Google.
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "https://aldi-test.invalid")
    with socket.socket() as proxy:
        proxy.bind(("127.0.0.1", 0))
        proxy.listen()
        monkeypatch.setenv("HTTPS_PROXY", f"{scheme}127.0.0.1:{proxy.getsockname()[1]}")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        asked = []

        def accept():
            connection, _address = proxy.accept()
            # Listening no longer: the client's retries are refused at once instead of waiting on an answer.
            proxy.close()
            with connection:
                asked.append(connection.recv(1024).split(b"\r\n", 1)[0])

        listener = threading.Thread(target=accept, daemon=True)
        listener.start()
        with pytest.raises(Exception):  # noqa: B017  the proxy hangs up: what matters is that it was asked
            GeminiTranslator(make_client("AIza-de-prueba"), chain=(FLASH,), sleep=lambda _s: None).translate_texts(
                ["Hard."], {}
            )
        listener.join(timeout=10)

    assert asked == [b"CONNECT aldi-test.invalid:443 HTTP/1.1"]


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


@pytest.mark.parametrize(
    ("failures", "calls"),
    [
        ({FLASH: key_error(401)}, [FLASH, FLASH]),
        ({FLASH: key_error(400)}, [FLASH, FLASH]),
        ({model: key_error(403) for model in MODEL_CHAIN}, [FLASH, FLASH, LITE, GEMMA]),
    ],
    ids=["401", "400 API_KEY_INVALID", "403 from every model"],
)
def test_a_key_refused_mid_job_pauses_it_until_she_saves_a_new_key(
    app, gemini, translator_keys, documents_dir, tmp_path, failures, calls
):
    for model, failure in failures.items():
        gemini.fail(model, failure, after=int(model == FLASH))

    job_id = app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES))["id"]
    paused = app.wait_for(job_id, until=lambda job: job["state"] == "paused")

    assert paused["notice"] == "Tu clave de Gemini dejó de funcionar. Revisa la clave en Ajustes."
    assert paused["page"] == 1
    time.sleep(0.3)
    assert gemini.calls == calls
    app.send("PUT", "/api/settings", {"key": "AIza-valida-2"})
    job = app.wait_for(job_id)
    assert job["state"] == "done"
    assert job["notice"] == ""
    assert job["failed_pages"] == []
    assert translator_keys == [VALID_KEY, "AIza-valida-2"]
    assert_every_page_once_in_order(documents_dir, "Esmalte")


def test_a_model_that_refuses_access_passes_the_job_to_the_next(app, gemini, clock, tmp_path):
    # Bug: a 403 from one model (Gemma not open to her key) paused the whole Job for a new key.
    gemini.fail(FLASH, quota_error())
    gemini.fail(LITE, quota_error())
    gemini.fail(GEMMA, key_error(403))
    clock.hold()

    job_id = app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES[:1]))["id"]
    job = app.wait_for(job_id, until=lambda job: job["state"] == "paused")

    assert job["notice"] == "En pausa hasta que Gemini vuelva a estar disponible"
    clock.release()


def test_a_model_refusing_access_moves_the_request_down_the_chain(translator, gemini):
    gemini.fail(FLASH, key_error(403))

    translation = translator.translate_texts(["The enamel is hard."], {})

    assert translation.texts == ["ES: The enamel is hard."]
    assert gemini.calls == [FLASH, LITE]


def test_a_403_while_the_other_models_cool_down_from_quota_is_not_a_refused_key(translator, gemini):
    # Bug: with flash and flash-lite skipped for a long quota cooldown, Gemma's 403 was the only answer asked, so the
    # key counted as refused and the Job waited for a new key that she did not need.
    gemini.fail(FLASH, quota_error("3600s"))
    gemini.fail(LITE, quota_error("3600s"))
    gemini.fail(GEMMA, key_error(403), key_error(403))

    with pytest.raises(QuotaExhausted):
        translator.translate_texts(["The enamel is hard."], {})
    with pytest.raises(QuotaExhausted):
        translator.translate_texts(["The enamel is hard."], {})

    assert gemini.calls == [FLASH, LITE, GEMMA, GEMMA]


def test_an_error_report_names_the_model_that_failed_not_the_whole_chain(app, gemini, github, tmp_path):
    invalid = errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
    gemini.fail(FLASH, quota_error())
    gemini.fail(LITE, invalid, invalid, invalid)

    job = app.wait_for(app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES[:1]))["id"])

    assert job["failed_pages"] == [1]
    [report] = github.reports()
    assert "- Gemini model: gemini-3.5-flash-lite\n" in report["body"]
    assert "- Gemini error: 400 INVALID_ARGUMENT\n" in report["body"]


def test_an_error_report_about_an_unusable_page_batch_answer_names_its_model(app, gemini, github, tmp_path):
    # Bug: the model of a Page Batch answer was dropped, so its report said `Gemini model: none`.
    gemini.fail(FLASH, "not JSON", "not JSON", "not JSON")

    job = app.wait_for(app.upload(build_scanned_pdf(tmp_path / "Encia.pdf", [[]]))["id"])

    assert job["failed_pages"] == [1]
    [report] = github.reports()
    assert "- Gemini model: gemini-3.5-flash\n" in report["body"]


def test_closing_the_app_ends_a_busy_models_wait_at_once(start_app, gemini, documents_dir, tmp_path):
    gemini.fail(FLASH, *[busy_error() for _ in range(4)])
    app = start_app(make_translator=lambda wait: GeminiTranslator(gemini, sleep=wait))
    job_id = app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES))["id"]
    wait_until(lambda: gemini.calls)

    started = time.monotonic()
    app.stop()

    assert time.monotonic() - started < 5
    assert gemini.calls == [FLASH]
    job = start_app(FakeTranslator()).wait_for(job_id)
    assert job["state"] == "done"
    assert_every_page_once_in_order(documents_dir, "Esmalte")


def test_a_blank_translation_of_a_paragraph_is_asked_again_not_delivered_empty(app, gemini, documents_dir, tmp_path):
    # Bug: `{"spanish": [""]}` passed as a translation, and the page arrived without its paragraph.
    blank = json.dumps({"spanish": ["  "], "terms": []})
    gemini.fail(FLASH, blank)

    job = app.wait_for(app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES))["id"])

    assert job["failed_pages"] == []
    assert_every_page_once_in_order(documents_dir, "Esmalte")


def test_a_page_translated_blank_every_time_becomes_a_failed_page(app, gemini, tmp_path):
    blank = json.dumps({"spanish": [""], "terms": []})
    gemini.fail(FLASH, blank, blank, blank)

    job = app.wait_for(app.upload(build_born_digital_pdf(tmp_path / "Esmalte.pdf", PAGES))["id"])

    assert job["state"] == "done"
    assert job["failed_pages"] == [1]
