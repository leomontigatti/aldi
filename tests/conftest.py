"""Test support: the fakes at the seams (translator, Gemini's client, the clock), the builders of test PDFs, and the
app running on a free port."""

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
from aldi.settings import Settings
from aldi.translator import TextTranslation

VALID_KEY = "AIza-valida-1"


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


class FakeGemini:
    """Stands in for google-genai's client: every model answers each block prefixed with `ES:`, unless `fail`
    scripted an error for that model's next request."""

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
        blocks = json.loads(contents)["blocks"]
        answer = config.response_schema(spanish=[f"ES: {block}" for block in blocks], terms=[])
        return SimpleNamespace(parsed=answer, text=answer.model_dump_json())


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


class RunningApp:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def get(self, path: str):
        with urllib.request.urlopen(self.base_url + path) as response:
            return json.load(response)

    def send(self, method: str, path: str, body: dict):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode("utf-8"),
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
def documents_dir(tmp_path):
    return tmp_path / "Documentos"


@pytest.fixture
def settings(tmp_path):
    """The settings with a key already saved, as on every launch after the first."""
    settings = Settings(tmp_path / "config")
    settings.save_key(VALID_KEY)
    return settings


@pytest.fixture
def key_check():
    return FakeKeyCheck()


@pytest.fixture
def translator_keys():
    """The key each translator the app built was given, in order."""
    return []


@pytest.fixture
def app(tmp_path, translator, documents_dir, settings, key_check, translator_keys, clock):
    """The app on a free port; each translator it builds is the fake, whatever the key."""

    def make_translator(key):
        translator_keys.append(key)
        return translator

    server = create_server(
        data_dir=tmp_path / "data",
        documents_dir=documents_dir,
        settings=settings,
        make_translator=make_translator,
        check_key=key_check,
        host="127.0.0.1",
        port=0,
        sleep=clock.sleep,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield RunningApp(f"http://{host}:{port}")
    server.shutdown()
    server.server_close()
