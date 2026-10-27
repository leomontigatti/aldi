"""Test support: the fake translator, the builders of test PDFs, and the app running on a free port."""

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
from aldi.translator import TextTranslation


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


class RunningApp:
    def __init__(self, server):
        self.server = server
        host, port = server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
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

    def post(self, path: str):
        request = urllib.request.Request(self.base_url + path, data=b"", method="POST")
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
def documents_dir(tmp_path):
    return tmp_path / "Documentos"


@pytest.fixture
def opened():
    """The folders the app opened in her file browser."""
    return []


@pytest.fixture
def start_app(tmp_path, documents_dir, opened):
    """Starts the app on this test's data and Documents folders; each call is a fresh start of the same install."""
    running = []

    def start(translator, open_folder=opened.append) -> RunningApp:
        server = create_server(
            data_dir=tmp_path / "data",
            documents_dir=documents_dir,
            translator=translator,
            open_folder=open_folder,
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
