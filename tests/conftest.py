"""Test support: the fakes at the seams (translator, Gemini's client, the clock, GitHub), the builders of test PDFs,
and the app running on a free port."""

import json
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

import httpx
import pymupdf
import pytest
from google.genai import errors

from aldi.app import create_server
from aldi.reports import GitHubUnavailable, Issue
from aldi.settings import Settings
from aldi.translator import TextTranslation

VALID_KEY = "AIza-valida-1"
REPORT_TOKEN = "github_pat_11AAprueba"


class FakeTranslator:
    """Stands in for Gemini at the translator seam: each text comes back prefixed with `ES:`.

    A page image is read as the blocks `scanned` holds for its page number, or else as one paragraph `ES: Page N`.
    """

    def __init__(self):
        self.requests: list[list[str]] = []
        self.batches: list[list[int]] = []
        """The page numbers of each Page Batch asked for, in order."""
        self.scanned: dict[int, list[dict]] = {}

    def translate_texts(self, texts: Sequence[str], terms: Mapping[str, str]) -> TextTranslation:
        self.requests.append(list(texts))
        return TextTranslation(texts=[f"ES: {text}" for text in texts], terms=dict(terms))

    def read_page_batch(self, pages: Sequence[tuple[int, bytes]], terms: Mapping[str, str]) -> str:
        self.batches.append([number for number, _image in pages])
        assert all(image.startswith(b"\x89PNG") for _number, image in pages)
        return json.dumps(
            {
                "pages": [{"page": number, "blocks": self.page_blocks(number)} for number, _image in pages],
                "terms": [{"original": original, "spanish": spanish} for original, spanish in terms.items()],
            }
        )

    def page_blocks(self, number: int) -> list[dict]:
        return self.scanned.get(number, [{"kind": "paragraph", "text": f"ES: Page {number}"}])


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


SCAN_SIZE = (620, 877)
"""A scanned page's image in pixels: an A4 page at 75 dpi."""


def build_scanned_pdf(path: Path, pages: Sequence[Sequence[tuple[tuple[int, int, int, int], tuple]]]) -> Path:
    """A PDF of page photos with no text layer: each page a white image with the given rectangles painted on it.

    A rectangle is its box, x0, y0, x1, y1 normalized 0 to 1000 like a Figure's, and its RGB colour.
    """
    document = pymupdf.open()
    width, height = SCAN_SIZE
    for rectangles in pages:
        page = document.new_page()
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
        image.set_rect(image.irect, (255, 255, 255))
        for (x0, y0, x1, y1), colour in rectangles:
            # Rounded outwards, so that a crop of exactly the box holds nothing but the colour.
            box = pymupdf.Rect(x0 * width, y0 * height, x1 * width, y1 * height) / 1000
            image.set_rect(box.round(), colour)
        page.insert_image(page.rect, stream=image.tobytes("png"))
    document.save(path)
    return path


class FakeGemini:
    """Stands in for google-genai's client: every model answers each block prefixed with `ES:`, and each page of a Page
    Batch as one paragraph `ES: Page N`, unless `fail` scripted an error for that model's next request. A Gemma model
    answers 500 to a response schema, as Gemma does."""

    def __init__(self):
        self.models = self
        self.calls: list[str] = []
        """The model each request went to, in order."""
        self._script: dict[str, list[Exception | None]] = {}

    def fail(self, model: str, *failures: Exception, after: int = 0) -> None:
        """The model's requests after its next `after` answers raise `failures`, one each."""
        self._script[model] = [None] * after + list(failures)

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        script = self._script.get(model)
        if script and (failure := script.pop(0)):
            raise failure
        if model.startswith("gemma") and config.response_schema:
            raise errors.ServerError(500, {"error": {"code": 500, "status": "INTERNAL"}})
        if isinstance(contents, list):
            # A Page Batch: each image follows its `Page N` line, and is read as one paragraph `ES: Page N`.
            numbers = [
                int(part.text.removeprefix("Page ")) for part in contents if (part.text or "").startswith("Page ")
            ]
            pages = [{"page": n, "blocks": [{"kind": "paragraph", "text": f"ES: Page {n}"}]} for n in numbers]
            return SimpleNamespace(text=json.dumps({"pages": pages, "terms": []}))
        blocks = json.loads(contents)["blocks"]
        return SimpleNamespace(text=json.dumps({"spanish": [f"ES: {block}" for block in blocks], "terms": []}))


def quota_error(retry_delay: str | None = None) -> errors.APIError:
    """A 429 as Gemini sends it, with its `RetryInfo` when `retry_delay` (`"38s"`) is given."""
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}] if retry_delay else []
    error = {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota", "details": details}
    return errors.ClientError(429, {"error": error})


def busy_error() -> errors.APIError:
    return errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "Overloaded"}})


def offline_error() -> httpx.ConnectError:
    return httpx.ConnectError("[Errno -3] Temporary failure in name resolution")


class FakeClock:
    """Stands in for the clock and every wait (`time.monotonic`, `time.sleep`): a wait is recorded and moves `now`
    on at once, or, after `hold`, blocks the waiting thread until `release` so a test can see the Job while it waits."""

    def __init__(self):
        self.waits: list[float] = []
        self._now = 0.0
        self._held: threading.Event | None = None

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self._now += seconds
        if held := self._held:
            held.wait(timeout=30)

    def hold(self) -> None:
        self._held = threading.Event()

    def release(self) -> None:
        held, self._held = self._held, None
        held.set()


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

    def wait_for(self, job_id: str, timeout: float = 60, until: Callable[[dict], bool] | None = None) -> dict:
        """The Job once it is done or failed, or once `until` holds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(f"/api/jobs/{job_id}")
            if until(job) if until else job["state"] in ("done", "failed"):
                return job
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not finish: {job}")


@pytest.fixture
def translator():
    return FakeTranslator()


@pytest.fixture
def clock():
    return FakeClock()


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
def start_app(tmp_path, documents_dir, settings, key_check, translator_keys, opened, github, clock):
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
            sleep=clock.sleep,
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
