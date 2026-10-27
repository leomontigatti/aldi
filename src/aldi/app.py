"""The app server: a Spanish single-page UI and a small JSON API on 127.0.0.1."""

import argparse
import json
import logging
import sys
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

import platformdirs

from aldi import instance
from aldi.estimate import estimate
from aldi.jobs import JobStore, LayoutNotOffered, SourceDocument, closing_wait
from aldi.page_range import PageRangeError
from aldi.reports import GitHub, Reporter
from aldi.results import open_folder as open_in_file_browser
from aldi.settings import Settings, default_config_dir
from aldi.source import NotAPdfError
from aldi.translator import Translator
from aldi.update import NOTHING_NEWER, Reinstaller, Updates, installed_version

# Her largest book is 52 MB; anything far beyond that is not a document she would drop.
_MAX_UPLOAD = 500 * 1024 * 1024
_NOT_A_PDF = "Este archivo no es un PDF."
_NO_KEY = "Primero guarda tu clave de Gemini."
_INVALID_KEY = "Esa clave no funciona. Revisa que la copiaste entera, sin espacios, y vuelve a pegarla."
_KEY_UNCHECKED = "No se pudo comprobar la clave. Revisa tu conexión a internet e inténtalo de nuevo."
_LAYOUT_NOT_OFFERED = (
    "Mantener el diseño solo se puede elegir si todas las páginas elegidas tienen texto seleccionable."
)


class AldiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address,
        jobs: JobStore,
        reporter: Reporter,
        settings: Settings,
        check_key: Callable[[str], bool],
        updates: Updates,
    ):
        super().__init__(address, _Handler)
        self.jobs = jobs
        self.reporter = reporter
        self.settings = settings
        self.check_key = check_key
        self.updates = updates
        self.close_window: Callable[[], None] = lambda: None
        """Closes the app's window, as `Actualizar` does once the update is ready; `main` sets it."""
        self.focus_window: Callable[[], None] = lambda: None
        """Brings the app's window to the front, as a second launch asks; `main` sets it."""

    def server_close(self):
        super().server_close()
        self.jobs.close()
        self.reporter.close()


class _TranslatorForKey:
    """The translator for the key saved now: a key changed in the settings applies from the next request on."""

    def __init__(self, settings: Settings, make_translator: Callable[[str], Translator]):
        self._settings = settings
        self._make_translator = make_translator
        self._key = ""
        self._translator: Translator | None = None

    def __getattr__(self, name: str):
        key = self._settings.key
        if self._translator is None or key != self._key:
            self._key, self._translator = key, self._make_translator(key)
        return getattr(self._translator, name)


def create_server(
    data_dir: Path,
    documents_dir: Path,
    settings: Settings,
    make_translator: Callable[[str, Callable[[float], None]], Translator],
    check_key: Callable[[str], bool],
    make_github: Callable[[str], GitHub],
    open_folder: Callable[[Path], None] = open_in_file_browser,
    latest_release: Callable[[], str] | None = None,
    reinstaller: Reinstaller | None = None,
    version: str | None = None,
    report_interval: float = 300,
    host: str = "127.0.0.1",
    port: int = 0,
    sleep: Callable[[float], None] | None = None,
) -> AldiServer:
    """The server, bound and ready for `serve_forever`; port 0 takes a free one.

    `make_translator` builds the translator for a key, given the wait it waits with between retries, which closing
    the app ends at once; `check_key` asks Gemini whether a key works; `make_github`
    builds the client Error Reports are filed with, for the reporting token; `sleep` stands in for the Job store's
    waits (a Quota Pause, no connection) in tests. `latest_release` asks GitHub for the latest Release's tag and
    `reinstaller` installs it; without them, as in development, no update is offered. Jobs left unfinished in
    `data_dir` by the last run start again, in the order they were dropped; queued Error Reports are sent every
    `report_interval` seconds.
    """
    closing = threading.Event()
    translator = _TranslatorForKey(settings, lambda key: make_translator(key, closing_wait(closing)))
    reporter = Reporter(
        data_dir,
        lambda: settings.report_token,
        make_github,
        secrets=lambda: [settings.key, settings.report_token],
        interval=report_interval,
    )
    jobs = JobStore(data_dir, documents_dir, translator, open_folder, reporter, sleep, closing)
    updates = Updates(data_dir, version or installed_version(), latest_release, reinstaller, reporter)
    return AldiServer((host, port), jobs, reporter, settings, check_key, updates)


class _Handler(BaseHTTPRequestHandler):
    server: AldiServer

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            page = resources.files("aldi").joinpath("ui/index.html").read_bytes()
            return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
        if path == "/api/settings":
            return self._json(HTTPStatus.OK, self._settings())
        if path == "/api/update":
            updates = self.server.updates
            return self._json(HTTPStatus.OK, {"available": updates.available(), "notice": updates.notice})
        if path == "/api/jobs":
            return self._json(HTTPStatus.OK, {"jobs": [asdict(job) for job in self.server.jobs.list()]})
        if path.startswith("/api/documents/") and path.endswith("/estimate"):
            return self._estimate(path.removeprefix("/api/documents/").removesuffix("/estimate"))
        if path.startswith("/api/jobs/"):
            job = self.server.jobs.get(path.removeprefix("/api/jobs/"))
            if job:
                return self._json(HTTPStatus.OK, asdict(job))
        self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})

    def do_PUT(self):
        if urllib.parse.urlsplit(self.path).path != "/api/settings":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
        try:
            key = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))["key"].strip()
        except (ValueError, TypeError, KeyError, AttributeError):
            key = ""
        if not key:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": _INVALID_KEY})
        try:
            valid = self.server.check_key(key)
        except Exception:
            return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": _KEY_UNCHECKED})
        if not valid:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": _INVALID_KEY})
        self.server.settings.save_key(key)
        self.server.jobs.key_saved()
        self._json(HTTPStatus.OK, self._settings())

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/jobs/") and path.endswith("/open-folder"):
            return self._open_folder(path.removeprefix("/api/jobs/").removesuffix("/open-folder"))
        if path.startswith("/api/jobs/") and path.endswith("/retry"):
            return self._retry(path.removeprefix("/api/jobs/").removesuffix("/retry"))
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            return self._cancel(path.removeprefix("/api/jobs/").removesuffix("/cancel"))
        if path == "/api/update":
            return self._update()
        if path == "/api/window/front":
            # A second launch of the app, which ends once its window is here.
            self.server.focus_window()
            return self._json(HTTPStatus.OK, {})
        if path not in ("/api/documents", "/api/jobs"):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
        if not self.server.settings.key:
            return self._json(HTTPStatus.CONFLICT, {"error": _NO_KEY})
        if path == "/api/jobs":
            return self._start()
        self._drop()

    def _drop(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= _MAX_UPLOAD:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": _NOT_A_PDF})
        pdf = self.rfile.read(length)
        if b"%PDF-" not in pdf[:1024]:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": _NOT_A_PDF})
        filename = urllib.parse.unquote(self.headers.get("X-Filename") or "documento.pdf")
        try:
            document = self.server.jobs.drop(filename, pdf)
        except NotAPdfError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": _NOT_A_PDF})
        body = {"id": document.id, "name": document.name, "pages": len(document.scanned)}
        self._json(HTTPStatus.CREATED, body | _estimates(document, range(1, len(document.scanned) + 1)))

    def _start(self):
        """Starts the Job of a dropped PDF on the Page Range she typed."""
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            document_id, page_range = str(body["document"]), str(body.get("pages") or "")
            output_mode = "layout" if body.get("output_mode") == "layout" else "reading"
        except (ValueError, TypeError, KeyError, AttributeError):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "No encontrado."})
        try:
            job = self.server.jobs.start(document_id, page_range, output_mode)
        except PageRangeError as error:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except LayoutNotOffered:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": _LAYOUT_NOT_OFFERED})
        if not job:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
        self._json(HTTPStatus.CREATED, asdict(job))

    def _estimate(self, document_id: str):
        """How long the Page Range in `?pages=` takes in each Output Mode it allows, or why it names no pages."""
        document: SourceDocument | None = self.server.jobs.dropped(document_id)
        if not document:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        try:
            pages = document.pages(query.get("pages", [""])[0])
        except PageRangeError as error:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        self._json(HTTPStatus.OK, {"pages": len(pages)} | _estimates(document, pages))

    def _update(self):
        try:
            ready = self.server.updates.update()
        except LookupError:
            return self._json(HTTPStatus.CONFLICT, {"error": NOTHING_NEWER})
        if not ready:
            return self._json(HTTPStatus.BAD_GATEWAY, {"error": self.server.updates.notice})
        self._json(HTTPStatus.OK, {"restarting": True})
        # After the answer, so the window shows that Aldi restarts before it closes.
        threading.Timer(0.5, self.server.close_window).start()

    def _retry(self, job_id: str):
        if not self.server.jobs.get(job_id):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
        job = self.server.jobs.retry(job_id)
        if not job:
            return self._json(HTTPStatus.CONFLICT, {"error": "Esta traducción no tiene páginas fallidas."})
        self._json(HTTPStatus.OK, asdict(job))

    def _cancel(self, job_id: str):
        cancelled = self.server.jobs.cancel(job_id)
        if cancelled is None:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
        if not cancelled:
            return self._json(HTTPStatus.CONFLICT, {"error": "Esta traducción ya terminó."})
        self._json(HTTPStatus.OK, {})

    def _open_folder(self, job_id: str):
        try:
            opened = self.server.jobs.open_folder(job_id)
        except OSError:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "No se pudo abrir la carpeta."})
        if not opened:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
        self._json(HTTPStatus.OK, {})

    def _settings(self) -> dict:
        """What the UI may know of the settings: never the key itself."""
        return {"has_key": bool(self.server.settings.key)}

    def _json(self, status: HTTPStatus, body: dict):
        self._send(status, json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _estimates(document: SourceDocument, pages) -> dict:
    """The Estimate of `pages` for the Reading Version, and for Mantener el diseño, None when it is not offered."""
    scanned = [document.scanned[number - 1] for number in pages]
    layout = estimate(scanned, layout=True) if document.keeps_layout(pages) else None
    return {"estimate": estimate(scanned), "layout_estimate": layout}


def main(
    argv: list[str] | None = None,
    open_window: Callable[[str], None] | None = None,
    focus_window: Callable[[], None] | None = None,
) -> None:
    """`aldi`: the app in its own window until she closes it; `aldi setup`: the install line's last step.

    `open_window` shows the app's URL and returns when the window is closed, and `focus_window` brings that window
    to the front; pywebview's by default. A launch while the app already runs on the same data folder brings the
    running app's window to the front and ends there.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["setup"]:
        from aldi.install import main as setup

        sys.exit(setup())

    # What Gemini answers and every wait, in the terminal the app runs in: what a stall is diagnosed from.
    logging.basicConfig(format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("aldi").setLevel(logging.INFO)
    from aldi.gemini import GeminiTranslator, make_client, validate_key
    from aldi.reports import GitHubClient
    from aldi.update import UvReinstaller, installed_from_release, latest_release

    parser = argparse.ArgumentParser(prog="aldi")
    parser.add_argument("--data-dir", type=Path, default=Path(platformdirs.user_data_dir("Aldi", appauthor=False)))
    parser.add_argument("--documents-dir", type=Path, default=Path(platformdirs.user_documents_dir()))
    parser.add_argument("--config-dir", type=Path, default=default_config_dir())
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-window", action="store_true", help="serve the app until Ctrl-C, without its window")
    args = parser.parse_args(argv)
    lock = instance.hold(args.data_dir)
    if lock is None:
        if not instance.bring_to_front(args.data_dir):
            print("Aldi ya está abierto.", file=sys.stderr)
        return
    settings = Settings(args.config_dir, dev_env_file=Path(".env"))

    def make_translator(key: str, wait: Callable[[float], None]) -> GeminiTranslator:
        return GeminiTranslator(make_client(key), sleep=wait)

    release = installed_from_release()
    server = create_server(
        args.data_dir,
        args.documents_dir,
        settings,
        make_translator,
        validate_key,
        GitHubClient,
        latest_release=latest_release if release else None,
        reinstaller=UvReinstaller(args.data_dir, installed_version()) if release else None,
        port=args.port,
    )
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    instance.serving(args.data_dir, port)
    print(f"Aldi: {url}", flush=True)
    if args.no_window:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    serving = threading.Thread(target=server.serve_forever, daemon=True, name="aldi-server")
    serving.start()
    if open_window is None:
        open_window, server.close_window, focus_window = _native_window, _close_native_window, _focus_native_window
    server.focus_window = focus_window or (lambda: None)
    try:
        open_window(url)
    finally:
        server.shutdown()
        server.server_close()
        serving.join()
    server.updates.finish()


def _native_window(url: str) -> None:
    import webview

    webview.create_window("Aldi", url, width=960, height=800, min_size=(560, 480))
    webview.start()


def _focus_native_window() -> None:
    import webview

    for window in list(webview.windows):
        window.restore()
        window.show()
        # pywebview has no call for "to the front": on top for a moment brings it there on every system.
        window.on_top = True
        window.on_top = False


def _close_native_window() -> None:
    import webview

    for window in list(webview.windows):
        window.destroy()
