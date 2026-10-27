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
from aldi.results import open_folder as open_in_file_browser
from aldi.translator import Translator

# Her largest book is 52 MB; anything far beyond that is not a document she would drop.
_MAX_UPLOAD = 500 * 1024 * 1024
_NOT_A_PDF = "Este archivo no es un PDF."


class AldiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, jobs: JobStore):
        super().__init__(address, _Handler)
        self.jobs = jobs

    def server_close(self):
        super().server_close()
        self.jobs.close()


def create_server(
    data_dir: Path,
    documents_dir: Path,
    translator: Translator,
    open_folder: Callable[[Path], None] = open_in_file_browser,
    host: str = "127.0.0.1",
    port: int = 0,
) -> AldiServer:
    """The server, bound and ready for `serve_forever`; port 0 takes a free one.

    Jobs left unfinished in `data_dir` by the last run start again, in the order they were dropped.
    """
    return AldiServer((host, port), JobStore(data_dir, documents_dir, translator, open_folder))


class _Handler(BaseHTTPRequestHandler):
    server: AldiServer

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            page = resources.files("aldi").joinpath("ui/index.html").read_bytes()
            return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
        if path == "/api/jobs":
            return self._json(HTTPStatus.OK, {"jobs": [asdict(job) for job in self.server.jobs.list()]})
        if path.startswith("/api/jobs/"):
            job = self.server.jobs.get(path.removeprefix("/api/jobs/"))
            if job:
                return self._json(HTTPStatus.OK, asdict(job))
        self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/jobs/") and path.endswith("/open-folder"):
            return self._open_folder(path.removeprefix("/api/jobs/").removesuffix("/open-folder"))
        if path != "/api/jobs":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= _MAX_UPLOAD:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": _NOT_A_PDF})
        pdf = self.rfile.read(length)
        if b"%PDF-" not in pdf[:1024]:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": _NOT_A_PDF})
        filename = urllib.parse.unquote(self.headers.get("X-Filename") or "documento.pdf")
        job = self.server.jobs.start(filename, pdf)
        self._json(HTTPStatus.CREATED, asdict(job))

    def _open_folder(self, job_id: str):
        try:
            opened = self.server.jobs.open_folder(job_id)
        except OSError:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "No se pudo abrir la carpeta."})
        if not opened:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
        self._json(HTTPStatus.OK, {})

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
    from aldi.gemini import GeminiTranslator, read_key

    parser = argparse.ArgumentParser(prog="aldi")
    parser.add_argument("--data-dir", type=Path, default=Path(platformdirs.user_data_dir("Aldi", appauthor=False)))
    parser.add_argument("--documents-dir", type=Path, default=Path(platformdirs.user_documents_dir()))
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    server = create_server(args.data_dir, args.documents_dir, GeminiTranslator(read_key()), port=args.port)
    host, port = server.server_address[:2]
    print(f"Aldi: http://{host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
