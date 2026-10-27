"""Error Reports (ADR 4): a public issue on the app's repo for each bug, with nothing from her document in it.

A report is redacted when it is built, queued on disk, and sent from the queue: at once when it can be, and
later, once there is a connection and a token, when it cannot. A repeat of an open report's fingerprint becomes a
comment on it, and a daily cap stops a loop from flooding the tracker.
"""

import getpass
import hashlib
import importlib.metadata
import json
import platform
import re
import sysconfig
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from google.genai import errors as genai_errors

import aldi
from aldi.gemini import MODEL_CHAIN
from aldi.translator import Offline, QuotaExhausted

SENT = "Algo salió mal. Ya se envió un informe para que lo arreglen."
QUEUED = "Algo salió mal. El informe se enviará apenas haya conexión."

REPO = "leomontigatti/aldi"
LABELS = ["bug", "needs-triage", "source:app"]
# Reports and comments per day; the rest wait in the queue for the next day.
DAILY_CAP = 10
# How long before the token expires the maintainer is asked to renew it.
TOKEN_WARNING = timedelta(days=14)

# Where a stack frame's file may live; any other path is not shown at all.
_ROOTS = sorted(
    {Path(aldi.__file__).parent.parent}
    | {Path(sysconfig.get_paths()[name]) for name in ("stdlib", "platstdlib", "purelib", "platlib")},
    key=lambda root: len(str(root)),
    reverse=True,
)


class GitHubUnavailable(Exception):
    """GitHub could not be reached, or refused the token: the report stays queued."""


@dataclass(frozen=True)
class Issue:
    number: int
    body: str


class GitHub(Protocol):
    token_expires: datetime | None
    """When the token expires, as GitHub's last answer said; None before any answer or for a token with no end."""

    def open_reports(self) -> list[Issue]:
        """The repo's open issues labelled `source:app`."""
        ...

    def open_issue(self, title: str, body: str, labels: list[str]) -> int: ...

    def comment(self, number: int, body: str) -> None: ...


@dataclass(frozen=True)
class ErrorDetails:
    """What a report says about an error: its redacted stack trace, its fingerprint and Gemini's error."""

    trace: str
    fingerprint: str
    gemini_error: str
    summary: str
    """The exception's type and the app's function it was raised in, for the issue's title."""


@dataclass(frozen=True)
class Context:
    """What a report says about the Job, none of it from the document's text or name."""

    output_mode: str
    document_kind: str
    pages: int
    failed_pages: list[int]


def is_bug(error: BaseException) -> bool:
    """Whether `error` deserves a report: a Quota Pause, being offline, an invalid key and a 503 do not."""
    for link in _chain(error):
        if isinstance(link, QuotaExhausted | Offline):
            return False
        if isinstance(link, genai_errors.APIError):
            if link.code in (401, 403, 503):
                return False
            if link.code == 400 and "API_KEY_INVALID" in json.dumps(link.details, default=str):
                return False
    return True


def describe(error: BaseException) -> ErrorDetails:
    """The redacted account of `error`: types, files inside the app or Python, line numbers and function names.

    No exception message is kept: a message can quote the document, a path or the key.
    """
    lines: list[str] = []
    app_frames: list[str] = []
    gemini_error = ""
    for link in _chain(error):
        if lines:
            lines.append("")
        lines.append("Traceback (most recent call last):")
        for frame, line in traceback.walk_tb(link.__traceback__):
            where = _where(frame.f_code.co_filename)
            lines.append(f"  {where}:{line} in {frame.f_code.co_name}")
            if where.startswith("aldi/"):
                app_frames.append(f"{where}:{frame.f_code.co_name}")
        lines.append(_type_name(link))
        if isinstance(link, genai_errors.APIError):
            status = link.status if re.fullmatch(r"[A-Z_]+", str(link.status or "")) else ""
            gemini_error = f"{link.code} {status}".strip()
    types = [_type_name(link) for link in _chain(error)]
    fingerprint = hashlib.sha256("\n".join(types + app_frames).encode("utf-8")).hexdigest()[:12]
    raised_in = app_frames[-1].rpartition(":")[2] if app_frames else "unknown"
    return ErrorDetails("\n".join(lines), fingerprint, gemini_error, f"{types[-1].rpartition('.')[2]} in {raised_in}")


def token_expiry(header: str | None) -> datetime | None:
    """Parses GitHub's `GitHub-Authentication-Token-Expiration` header (`2026-12-01 00:00:00 UTC`)."""
    if not header:
        return None
    try:
        return datetime.strptime(header.strip().replace(" UTC", " +0000"), "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


class Reporter:
    """Files Error Reports from a queue in `data_dir/reports/`, and warns the maintainer before the token expires."""

    def __init__(
        self,
        data_dir: Path,
        token: Callable[[], str],
        make_github: Callable[[str], GitHub],
        secrets: Callable[[], list[str]] = list,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        interval: float = 300,
    ):
        """`token` reads the reporting token, empty when there is none; `secrets` lists what a report never carries."""
        self._folder = data_dir / "reports"
        self._queue = self._folder / "queue"
        self._token = token
        self._make_github = make_github
        self._secrets = secrets
        self._now = now
        self._interval = interval
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._send_now_and_then, daemon=True, name="aldi-reports")
        self._thread.start()

    def report(self, problem: str, details: ErrorDetails, context: Context) -> bool:
        """Queues a report and tries to send it; True when it went out, False when it waits in the queue."""
        title = f"Error Report: {problem}: {details.summary}"
        body = self._redact(_body(problem, details, context))
        with self._lock:
            self._queue.mkdir(parents=True, exist_ok=True)
            for path in self._queue.glob("*.json"):
                queued = json.loads(path.read_text(encoding="utf-8"))
                if queued["fingerprint"] == details.fingerprint:
                    # A loop while offline adds to the one queued report instead of piling up more.
                    queued["count"] += 1
                    path.write_text(json.dumps(queued, ensure_ascii=False), encoding="utf-8")
                    break
            else:
                path = self._queue / f"{self._now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}.json"
                item = {"fingerprint": details.fingerprint, "title": self._redact(title), "body": body, "count": 1}
                path.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
        self._send_safely()
        return not path.exists()

    def send(self) -> None:
        """Sends what is queued, up to the day's cap; checks the token's expiry once a day even with nothing queued."""
        with self._lock:
            token = self._token()
            queued = sorted(self._queue.glob("*.json")) if self._queue.is_dir() else []
            state = self._state()
            today = self._now().date().isoformat()
            if not token or (not queued and state.get("checked") == today):
                return
            if state.get("day") != today:
                state |= {"day": today, "sent": 0}
            try:
                github = self._make_github(token)
                issues = github.open_reports()
                state["checked"] = today
                self._save_state(state)
                for path in queued:
                    if state["sent"] >= DAILY_CAP:
                        break
                    item = json.loads(path.read_text(encoding="utf-8"))
                    self._send(github, issues, item)
                    path.unlink()
                    state["sent"] += 1
                    self._save_state(state)
                self._warn_before_expiry(github, issues)
            except GitHubUnavailable:
                return

    def close(self) -> None:
        self._closing.set()
        self._thread.join()

    def _send(self, github: GitHub, issues: list[Issue], item: dict) -> None:
        marker = _marker(item["fingerprint"])
        open_issue = next((issue for issue in issues if marker in issue.body), None)
        if open_issue:
            times = "Happened again" if item["count"] == 1 else f"Happened again, {item['count']} times"
            github.comment(open_issue.number, f"{times}:\n\n{item['body']}")
        else:
            number = github.open_issue(item["title"], item["body"], LABELS)
            issues.append(Issue(number, item["body"]))

    def _warn_before_expiry(self, github: GitHub, issues: list[Issue]) -> None:
        expires = github.token_expires
        if not expires or expires - self._now() > TOKEN_WARNING:
            return
        day = expires.date().isoformat()
        marker = _marker(f"token-{day}")
        if any(marker in issue.body for issue in issues):
            return
        body = (
            f"The token the app files Error Reports with expires on {day}. After that, reports wait in her laptop's "
            "queue until a new token is saved.\n\n"
            f"Create a fine-grained token with only Issues: read and write on `{REPO}`, and save it in the app's "
            f"settings on her laptop.\n\n{marker}"
        )
        number = github.open_issue(f"Renew the Error Report token before {day}", body, ["needs-triage", "source:app"])
        issues.append(Issue(number, body))

    def _redact(self, text: str) -> str:
        """The last net under `describe`: removes the secrets, her home folder and her user name if any slipped in."""
        home = str(Path.home())
        for secret in [*self._secrets(), home, home.replace("\\", "/")]:
            if len(secret) >= 4:
                text = text.replace(secret, "<redacted>")
        user = getpass.getuser()
        if len(user) >= 2:
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", "<user>", text, flags=re.IGNORECASE)
        return text

    def _send_now_and_then(self) -> None:
        while True:
            self._send_safely()
            if self._closing.wait(self._interval):
                return

    def _send_safely(self) -> None:
        try:
            self.send()
        except Exception:
            # A report that cannot go out stays queued; it must never fail the Job or stop the thread.
            pass

    def _state(self) -> dict:
        path = self._folder / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _save_state(self, state: dict) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        (self._folder / "state.json").write_text(json.dumps(state), encoding="utf-8")


class GitHubClient:
    """GitHub's REST API with the fine-grained token (Issues only, this repo)."""

    def __init__(self, token: str, repo: str = REPO):
        self._token = token
        self._repo = repo
        self.token_expires: datetime | None = None

    def open_reports(self) -> list[Issue]:
        query = urllib.parse.urlencode({"state": "open", "labels": "source:app", "per_page": 100})
        url: str | None = f"https://api.github.com/repos/{self._repo}/issues?{query}"
        issues = []
        while url:
            answer, headers = self._call("GET", url)
            issues += [Issue(item["number"], item.get("body") or "") for item in answer if "pull_request" not in item]
            url = _next_page(headers.get("Link", ""))
        return issues

    def open_issue(self, title: str, body: str, labels: list[str]) -> int:
        answer, _ = self._call(
            "POST",
            f"https://api.github.com/repos/{self._repo}/issues",
            {"title": title, "body": body, "labels": labels},
        )
        return answer["number"]

    def comment(self, number: int, body: str) -> None:
        self._call("POST", f"https://api.github.com/repos/{self._repo}/issues/{number}/comments", {"body": body})

    def _call(self, method: str, url: str, body: dict | None = None):
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "aldi",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                self.token_expires = token_expiry(response.headers.get("GitHub-Authentication-Token-Expiration"))
                return json.load(response), response.headers
        except (OSError, ValueError) as error:
            # urllib's URLError and HTTPError are OSErrors: no connection, a refused token, GitHub down.
            raise GitHubUnavailable(type(error).__name__) from error


def _body(problem: str, details: ErrorDetails, context: Context) -> str:
    failed = ", ".join(str(number) for number in context.failed_pages) or "none"
    return (
        "Filed by the app on its own (ADR 4).\n\n"
        f"- Problem: {problem}\n"
        f"- App version: {_version()}\n"
        f"- System: {_system()}\n"
        f"- Output Mode: {context.output_mode}\n"
        f"- Document: {context.document_kind}, {context.pages} pages\n"
        f"- Failed pages: {failed}\n"
        f"- Gemini model: {', '.join(MODEL_CHAIN)} (the Model Chain)\n"
        f"- Gemini error: {details.gemini_error or 'none'}\n\n"
        f"```text\n{details.trace}\n```\n\n"
        f"{_marker(details.fingerprint)}\n"
    )


def _marker(fingerprint: str) -> str:
    return f"Fingerprint: `{fingerprint}`"


def _version() -> str:
    try:
        return importlib.metadata.version("aldi")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _system() -> str:
    system = platform.system()
    if system == "Darwin":
        system = f"macOS {platform.mac_ver()[0]}"
    elif system == "Windows":
        system = f"Windows {platform.release()} ({platform.version()})"
    else:
        system = f"{system} {platform.release()}"
    return f"{system}, {platform.machine()}, Python {platform.python_version()}"


def _chain(error: BaseException) -> list[BaseException]:
    """`error` and its causes, the first cause first, the way a traceback prints them."""
    chain: list[BaseException] = []
    link: BaseException | None = error
    while link is not None and link not in chain:
        chain.append(link)
        link = link.__cause__ or (None if link.__suppress_context__ else link.__context__)
    return chain[::-1]


def _where(filename: str) -> str:
    if filename.startswith("<"):
        return filename
    path = Path(filename)
    for root in _ROOTS:
        if path.is_relative_to(root):
            return path.relative_to(root).as_posix()
    return "<elsewhere>"


def _type_name(error: BaseException) -> str:
    kind = type(error)
    return kind.__qualname__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}"


def _next_page(link: str) -> str | None:
    match = re.search(r'<([^>]+)>;\s*rel="next"', link)
    return match.group(1) if match else None
