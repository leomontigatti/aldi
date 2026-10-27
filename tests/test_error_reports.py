"""Error Reports: a bug files a public issue with nothing from her document, once per problem, capped (#15)."""

import getpass
from datetime import UTC, datetime, timedelta

import pytest
from google.genai import errors as genai_errors

from aldi.reports import DAILY_CAP, Context, Reporter, describe, token_expiry
from aldi.settings import Settings
from aldi.translator import QuotaExhausted
from conftest import REPORT_TOKEN, VALID_KEY, FakeGitHub, build_born_digital_pdf, wait_until

SENT = "Algo salió mal. Ya se envió un informe para que lo arreglen."
QUEUED = "Algo salió mal. El informe se enviará apenas haya conexión."
PRIVATE_TEXT = "Pulpal necrosis in the patient Corrêa"


def failing_always(translator, error):
    """Makes every request for the text on page 2 raise a new `error()`, as each failed request does."""
    translate = translator.translate_texts

    def translate_or_fail(texts, terms):
        if PRIVATE_TEXT in texts:
            raise error()
        return translate(texts, terms)

    translator.translate_texts = translate_or_fail


def document(tmp_path):
    folder = tmp_path / getpass.getuser() / "Visão"
    folder.mkdir(parents=True)
    return build_born_digital_pdf(folder / "Visão Corrêa.pdf", [["The pulp."], [PRIVATE_TEXT], ["The root."]])


def private_bits(source):
    return [PRIVATE_TEXT, "Pulpal", "Corrêa", "Visão", str(source.parent), source.parent.as_posix(), VALID_KEY]


def test_a_failed_page_files_a_redacted_report_and_tells_her(app, translator, github, tmp_path):
    source = document(tmp_path)
    leaky = f"bad answer for {PRIVATE_TEXT!r} in {source} with key {VALID_KEY} and {REPORT_TOKEN}"
    failing_always(translator, lambda: RuntimeError(leaky))

    job = app.wait_for(app.upload(source)["id"])

    assert job["state"] == "done"
    assert job["failed_pages"] == [2]
    assert job["notice"] == SENT
    [report] = github.reports()
    assert report["labels"] == ["bug", "needs-triage", "source:app"]
    assert github.tokens and set(github.tokens) == {REPORT_TOKEN}
    body = report["body"]
    assert "Output Mode: Reading Version" in body
    assert "Document: born-digital, 3 pages" in body
    assert "Failed pages: 2" in body
    assert "Gemini model: gemini-" in body
    assert "aldi/jobs.py" in body
    assert "RuntimeError" in report["title"]
    published = report["title"] + body
    for private in [*private_bits(source), REPORT_TOKEN]:
        assert private not in published
    assert getpass.getuser().lower() not in published.lower()


def test_a_result_that_cannot_be_written_is_reported(app, github, documents_dir, tmp_path):
    documents_dir.mkdir()
    (documents_dir / "Aldi").write_text("a file where the Aldi folder should be", encoding="utf-8")
    source = document(tmp_path)

    job = app.wait_for(app.upload(source)["id"])

    assert job["state"] == "failed"
    assert job["notice"] == SENT
    [report] = github.reports()
    assert "Result could not be written" in report["title"]
    assert "Failed pages: none" in report["body"]
    for private in private_bits(source):
        assert private not in report["title"] + report["body"]


def test_the_same_problem_again_comments_on_its_open_issue(app, translator, github, tmp_path):
    failing_always(translator, lambda: RuntimeError("unreadable"))
    source = document(tmp_path)

    app.wait_for(app.upload(source)["id"])
    job = app.wait_for(app.upload(source)["id"])

    assert job["notice"] == SENT
    [report] = github.reports()
    assert len(report["comments"]) == 1
    assert "Failed pages: 2" in report["comments"][0]


def test_a_closed_issue_does_not_take_the_repeat(app, translator, github, tmp_path):
    failing_always(translator, lambda: RuntimeError("unreadable"))
    source = document(tmp_path)
    app.wait_for(app.upload(source)["id"])
    github.issues[0]["open"] = False

    app.wait_for(app.upload(source)["id"])

    assert [len(report["comments"]) for report in github.reports()] == [0, 0]


@pytest.mark.parametrize(
    "error",
    [
        QuotaExhausted,
        lambda: genai_errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "busy"}}),
        lambda: genai_errors.ClientError(
            400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}}
        ),
    ],
    ids=["quota pause", "gemini busy", "invalid key"],
)
def test_what_is_not_a_bug_is_never_reported(app, translator, github, tmp_path, error):
    failing_always(translator, error)

    job = app.wait_for(app.upload(document(tmp_path))["id"])
    app.stop()

    assert job["notice"] == ""
    assert github.issues == []


def test_a_report_waits_for_the_connection_and_then_goes_out(app, translator, github, tmp_path):
    github.offline = True
    failing_always(translator, lambda: RuntimeError("unreadable"))

    job = app.wait_for(app.upload(document(tmp_path))["id"])

    assert job["notice"] == QUEUED
    assert github.issues == []
    github.offline = False
    wait_until(lambda: github.reports())
    assert len(github.reports()) == 1


def test_a_report_waits_for_the_token_and_survives_a_restart(start_app, translator, github, settings, tmp_path):
    Settings(tmp_path / "config").save_report_token("")
    failing_always(translator, lambda: RuntimeError("unreadable"))
    app = start_app(translator)

    job = app.wait_for(app.upload(document(tmp_path))["id"])
    app.stop()

    assert job["notice"] == QUEUED
    settings.save_report_token(REPORT_TOKEN)
    start_app(translator)
    wait_until(lambda: github.reports())
    assert len(github.reports()) == 1


def test_the_maintainer_is_asked_to_renew_the_token_once_before_it_expires(start_app, translator, github):
    github.token_expires = datetime.now(UTC) + timedelta(days=7)

    start_app(translator).stop()
    start_app(translator).stop()

    [renew] = github.issues
    assert renew["labels"] == ["needs-triage", "source:app"]
    assert github.token_expires.date().isoformat() in renew["title"]


def test_a_token_far_from_expiring_opens_nothing(start_app, translator, github):
    github.token_expires = datetime.now(UTC) + timedelta(days=60)

    start_app(translator).stop()

    assert github.tokens == [REPORT_TOKEN]
    assert github.issues == []


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 27, 9, tzinfo=UTC)

    def __call__(self):
        return self.now


def reports_of_problems(count):
    details = []
    for number in range(count):
        try:
            # A different exception type per problem gives each its own fingerprint.
            raise type(f"Problem{number}", (Exception,), {})()
        except Exception as error:
            details.append(describe(error))
    return details


def test_a_daily_cap_holds_and_the_rest_go_out_the_next_day(tmp_path):
    github = FakeGitHub()
    clock = Clock()
    reporter = Reporter(tmp_path, lambda: REPORT_TOKEN, github, now=clock, interval=3600)
    context = Context("Reading Version", "born-digital", 3, [2])

    sent = [reporter.report("Failed Pages", details, context) for details in reports_of_problems(DAILY_CAP + 2)]

    assert sent == [True] * DAILY_CAP + [False, False]
    assert len(github.reports()) == DAILY_CAP
    clock.now += timedelta(days=1)
    reporter.send()
    assert len(github.reports()) == DAILY_CAP + 2
    reporter.close()


def test_the_expiry_header_is_read_in_both_of_githubs_formats():
    assert token_expiry("2026-12-01 00:00:00 UTC") == datetime(2026, 12, 1, tzinfo=UTC)
    assert token_expiry("2026-12-01 03:00:00 +0300") == datetime(2026, 12, 1, tzinfo=UTC)
    assert token_expiry(None) is None
    assert token_expiry("soon") is None
