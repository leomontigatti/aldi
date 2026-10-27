"""Test support: the fake translator and GitHub, the builders of test PDFs, and the app running on a free port."""

import json
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path

import pymupdf
import pytest

from aldi.app import create_server
from aldi.reports import GitHubUnavailable, Issue
from aldi.settings import Settings
from aldi.translator import TextTranslation

VALID_KEY = "AIza-valida-1"
REPORT_TOKEN = "github_pat_11AAprueba"


class FakeTranslator:
    """Stands in for Gemini at the translator seam: each text comes back prefixed with `ES:`."""

    def __init__(self):
        self.requests: list[list[str]] = []

    def translate_texts(self, texts: Sequence[str], terms: Mapping[str, str]) -> TextTranslation:
        self.requests.append(list(texts))
        return TextTranslation(texts=[f"ES: {text}" for text in texts], terms=dict(terms))


def build_born_digital_pdf(path: Path, pages: Sequence[Sequence[str]]) -> Path:
    """A PDF with a text layer: each page holds the given paragraphs, one text block each."""
    document = pymupdf.open()
    for paragraphs in pages:
        page = document.new_page()
        y = 72
        for paragraph in paragraphs:
            box = pymupdf.Rect(72, y, page.rect.width - 72, y + 200)
            page.insert_textbox(box, paragraph, fontsize=11)
            y += 220
    document.save(path)
    return path


ARTICLE_HEADER = "Journal of Oral Science 2026; 12:"
ARTICLE_FIGURE_COLOR = (0x2E, 0x8B, 0x57)
ARTICLE_FIGURE_LABEL = "Occlusal lesion"
ARTICLE_TABLE = [["Tooth", "Eruption age"], ["Central incisor", "7 years"], ["First molar", "6 years"]]
ARTICLE_REFERENCES = [
    "1. Smith J, Muñoz A. Fluoride and caries. Caries Res. 2020;54:1-9.",
    "2. Corrêa M. Selantes em molares. Rev Odontol. 2019;12:33-40.",
]


def build_article_pdf(path: Path) -> Path:
    """A two-page journal article with a text layer, laid out the way her articles are.

    Page 1: a running header and page number, a title across the page, then two columns: on the left two
    paragraphs with bold and italic runs, on the right an image with a label printed over it, its caption under
    it and a paragraph; under both columns, a ruled table across the page. Page 2: one paragraph, the `References`
    heading (bold, at body size) and a references list with italic journal names.
    """
    document = pymupdf.open()
    css = "* {font-size: 10pt; margin: 0 0 8pt 0;}"
    for number in (1, 2):
        page = document.new_page()
        page.insert_text((50, 35), f"{ARTICLE_HEADER}{number}", fontsize=8)
        page.insert_text((290, 815), str(number), fontsize=8)
    first, second = document[0], document[1]
    first.insert_textbox(pymupdf.Rect(50, 60, 545, 100), "Early childhood caries: a review", fontsize=18)
    first.insert_htmlbox(
        pymupdf.Rect(50, 110, 285, 500),
        "<p>Dental caries is a <b>biofilm-mediated</b> disease of the <i>mineralized</i> tissues.</p>"
        "<p>Children in the Peñalolén cohort were examined every year.</p>",
        css=css,
    )
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), False)
    image.set_rect(image.irect, ARTICLE_FIGURE_COLOR)
    first.insert_image(pymupdf.Rect(310, 110, 545, 250), stream=image.tobytes("png"))
    first.insert_text((330, 180), ARTICLE_FIGURE_LABEL, fontsize=9)
    first.insert_htmlbox(
        pymupdf.Rect(310, 258, 545, 500),
        "<p>Figure 1. Radiograph of a carious molar.</p><p>The right column continues the text.</p>",
        css=css,
    )
    top, row_height, columns = 520, 20, (50, 300, 545)
    for row_number, row in enumerate(ARTICLE_TABLE):
        y = top + row_number * row_height
        for left, right, cell in zip(columns, columns[1:], row, strict=False):
            first.draw_rect(pymupdf.Rect(left, y, right, y + row_height), color=(0, 0, 0), width=0.5)
            first.insert_text((left + 4, y + 14), cell, fontsize=10)
    second.insert_htmlbox(
        pymupdf.Rect(50, 60, 545, 750),
        "<p>Sealants prevent caries in the first molars.</p><p><b>References</b></p>"
        + "".join(f"<p>{reference}</p>" for reference in ARTICLE_REFERENCES).replace(
            "Caries Res.", "<i>Caries Res.</i>"
        ),
        css=css,
    )
    document.save(path)
    return path


class FakeKeyCheck:
    """Stands in for the one cheap Gemini call that checks a key: a key starting `AIza-valida` passes."""

    def __init__(self):
        self.checked: list[str] = []
        self.error: Exception | None = None
        """Raised instead of answering, as when Gemini cannot be reached."""

    def __call__(self, key: str) -> bool:
        self.checked.append(key)
        if self.error:
            raise self.error
        return key.startswith("AIza-valida")


class FakeGitHub:
    """Stands in for GitHub's issues API on this repo; also the factory the app builds its client with."""

    def __init__(self):
        self.issues: list[dict] = []
        """Each issue filed: number, title, body, labels, comments and whether it is open."""
        self.offline = False
        self.token_expires = None
        self.tokens: list[str] = []

    def __call__(self, token: str) -> "FakeGitHub":
        self.tokens.append(token)
        return self

    def open_reports(self) -> list[Issue]:
        self._reach()
        return [Issue(i["number"], i["body"]) for i in self.issues if i["open"] and "source:app" in i["labels"]]

    def open_issue(self, title: str, body: str, labels: list[str]) -> int:
        self._reach()
        number = len(self.issues) + 1
        self.issues.append(
            {"number": number, "title": title, "body": body, "labels": labels, "comments": [], "open": True}
        )
        return number

    def comment(self, number: int, body: str) -> None:
        self._reach()
        self.issues[number - 1]["comments"].append(body)

    def reports(self) -> list[dict]:
        """The Error Reports filed, without the issues that ask to renew the token."""
        return [issue for issue in self.issues if "bug" in issue["labels"]]

    def _reach(self) -> None:
        if self.offline:
            raise GitHubUnavailable("offline")


class RunningApp:
    def __init__(self, server):
        self.server = server
        host, port = server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        # `shutdown` waits out the poll interval, 0.5 s by default: on every test's teardown.
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Closes the app the way she closes its window: the Job in hand stays on disk for the next start."""
        self.server.shutdown()
        self.server.server_close()
        self._thread.join()

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def get(self, path: str):
        with urllib.request.urlopen(self.base_url + path) as response:
            return json.load(response)

    def send(self, method: str, path: str, body: dict | None = None):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode("utf-8") if body is not None else b"",
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def upload(self, pdf: Path, filename: str | None = None):
        request = urllib.request.Request(
            self.base_url + "/api/jobs",
            data=pdf.read_bytes(),
            method="POST",
            headers={"X-Filename": urllib.parse.quote(filename or pdf.name)},
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def wait_for_page(self, job_id: str, page: int, timeout: float = 60) -> dict:
        """Waits until `page` pages of the Job are translated."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(f"/api/jobs/{job_id}")
            if job["page"] >= page:
                return job
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not reach page {page}: {job}")

    def wait_for(self, job_id: str, timeout: float = 60) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(f"/api/jobs/{job_id}")
            if job["state"] in ("done", "failed"):
                return job
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not finish: {job}")


@pytest.fixture
def translator():
    return FakeTranslator()


@pytest.fixture
def github():
    return FakeGitHub()


@pytest.fixture
def documents_dir(tmp_path):
    return tmp_path / "Documentos"


@pytest.fixture
def settings(tmp_path):
    """The settings with a key and the reporting token already saved, as on every launch after the first."""
    settings = Settings(tmp_path / "config")
    settings.save_key(VALID_KEY)
    settings.save_report_token(REPORT_TOKEN)
    return settings


@pytest.fixture
def key_check():
    return FakeKeyCheck()


@pytest.fixture
def translator_keys():
    """The key each translator the app built was given, in order."""
    return []


@pytest.fixture
def opened():
    """The folders the app opened in her file browser."""
    return []


@pytest.fixture
def start_app(tmp_path, documents_dir, settings, key_check, translator_keys, opened, github):
    """Starts the app on this test's data and Documents folders; each call is a fresh start of the same install.

    Each translator the app builds is the given fake, whatever the key.
    """
    running = []

    def start(translator, open_folder=opened.append) -> RunningApp:
        def make_translator(key):
            translator_keys.append(key)
            return translator

        server = create_server(
            data_dir=tmp_path / "data",
            documents_dir=documents_dir,
            settings=settings,
            make_translator=make_translator,
            check_key=key_check,
            open_folder=open_folder,
            make_github=github,
            report_interval=0.05,
            host="127.0.0.1",
            port=0,
        )
        running.append(RunningApp(server))
        return running[-1]

    yield start
    for app in running:
        if app.running:
            app.stop()


@pytest.fixture
def app(start_app, translator):
    return start_app(translator)
