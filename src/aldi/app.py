"""The app server: a Spanish single-page UI and a small JSON API on 127.0.0.1."""

import argparse
import json
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

import platformdirs

from aldi.jobs import JobStore
from aldi.reports import GitHub, Reporter
from aldi.results import open_folder as open_in_file_browser
from aldi.settings import Settings, default_config_dir
from aldi.translator import Translator

# Her largest book is 52 MB; anything far beyond that is not a document she would drop.
_MAX_UPLOAD = 500 * 1024 * 1024
_NOT_A_PDF = "Este archivo no es un PDF."
_NO_KEY = "Primero guarda tu clave de Gemini."
_INVALID_KEY = "Esa clave no funciona. Revisa que la copiaste entera, sin espacios, y vuelve a pegarla."
_KEY_UNCHECKED = "No se pudo comprobar la clave. Revisa tu conexión a internet e inténtalo de nuevo."


class AldiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address, jobs: JobStore, reporter: Reporter, settings: Settings, check_key: Callable[[str], bool]
    ):
        super().__init__(address, _Handler)
        self.jobs = jobs
        self.reporter = reporter
        self.settings = settings
        self.check_key = check_key

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
    make_translator: Callable[[str], Translator],
    check_key: Callable[[str], bool],
    make_github: Callable[[str], GitHub],
    open_folder: Callable[[Path], None] = open_in_file_browser,
    report_interval: float = 300,
    host: str = "127.0.0.1",
    port: int = 0,
) -> AldiServer:
    """The server, bound and ready for `serve_forever`; port 0 takes a free one.

    `make_translator` builds the translator for a key; `check_key` asks Gemini whether a key works; `make_github`
    builds the client Error Reports are filed with, for the reporting token. Jobs left unfinished in `data_dir` by
    the last run start again, in the order they were dropped; queued Error Reports are sent every
    `report_interval` seconds.
    """
    translator = _TranslatorForKey(settings, make_translator)
    reporter = Reporter(
        data_dir,
        lambda: settings.report_token,
        make_github,
        secrets=lambda: [settings.key, settings.report_token],
        interval=report_interval,
    )
    jobs = JobStore(data_dir, documents_dir, translator, open_folder, reporter)
    return AldiServer((host, port), jobs, reporter, settings, check_key)


class _Handler(BaseHTTPRequestHandler):
    server: AldiServer

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            page = resources.files("aldi").joinpath("ui/index.html").read_bytes()
            return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
        if path == "/api/settings":
            return self._json(HTTPStatus.OK, self._settings())
        if path == "/api/jobs":
            return self._json(HTTPStatus.OK, {"jobs": [asdict(job) for job in self.server.jobs.list()]})
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
        self._json(HTTPStatus.OK, self._settings())

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/jobs/") and path.endswith("/open-folder"):
            return self._open_folder(path.removeprefix("/api/jobs/").removesuffix("/open-folder"))
        if path.startswith("/api/jobs/") and path.endswith("/retry"):
            return self._retry(path.removeprefix("/api/jobs/").removesuffix("/retry"))
        if path != "/api/jobs":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
        if not self.server.settings.key:
            return self._json(HTTPStatus.CONFLICT, {"error": _NO_KEY})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= _MAX_UPLOAD:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": _NOT_A_PDF})
        pdf = self.rfile.read(length)
        if b"%PDF-" not in pdf[:1024]:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": _NOT_A_PDF})
        filename = urllib.parse.unquote(self.headers.get("X-Filename") or "documento.pdf")
        job = self.server.jobs.start(filename, pdf)
        self._json(HTTPStatus.CREATED, asdict(job))

    def _retry(self, job_id: str):
        if not self.server.jobs.get(job_id):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
        job = self.server.jobs.retry(job_id)
        if not job:
            return self._json(HTTPStatus.CONFLICT, {"error": "Esta traducción no tiene páginas fallidas."})
        self._json(HTTPStatus.OK, asdict(job))

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


def main(argv: list[str] | None = None) -> None:
    from aldi.gemini import GeminiTranslator, validate_key
    from aldi.reports import GitHubClient

    parser = argparse.ArgumentParser(prog="aldi")
    parser.add_argument("--data-dir", type=Path, default=Path(platformdirs.user_data_dir("Aldi", appauthor=False)))
    parser.add_argument("--documents-dir", type=Path, default=Path(platformdirs.user_documents_dir()))
    parser.add_argument("--config-dir", type=Path, default=default_config_dir())
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    settings = Settings(args.config_dir, dev_env_file=Path(".env"))
    server = create_server(
        args.data_dir, args.documents_dir, settings, GeminiTranslator, validate_key, GitHubClient, port=args.port
    )
    host, port = server.server_address[:2]
    print(f"Aldi: http://{host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
