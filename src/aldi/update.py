"""Updates from Releases: on launch the app asks GitHub for the latest Release, and `Actualizar` reinstalls it.

A Release is a tag the maintainer pushed (`v0.2.0`, the package's version with a `v`), never a branch head: the
check reads the latest published Release's tag, and the install takes that tag's archive. Her settings, Jobs and
results live outside the `uv` tool environment an update replaces, so an update never touches them.

The new version is downloaded and built while this one still runs, so most failures leave her where she was;
the reinstall itself waits for the app to exit (`aldi.reinstall`), and puts this version back when it fails.
"""

import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Protocol

from aldi.reports import REPO, ErrorDetails, Reporter, describe

LATEST_RELEASE = f"https://api.github.com/repos/{REPO}/releases/latest"
NOTHING_NEWER = "Ya tienes la última versión."

_TAG = re.compile(r"v(\d+(?:\.\d+)*)")
_FAILED = "No se pudo actualizar Aldi."
# Building a Release downloads its dependencies, pymupdf and pandoc among them.
_PREPARE_TIMEOUT = 30 * 60


def is_newer(tag: str, version: str) -> bool:
    """Whether the Release `tag` is a later version than `version`, the one running; False for any other tag."""
    match = _TAG.fullmatch(tag)
    if not match or not re.fullmatch(r"\d+(\.\d+)*", version):
        return False
    new = [int(part) for part in match.group(1).split(".")]
    current = [int(part) for part in version.split(".")]
    width = max(len(new), len(current))
    return new + [0] * (width - len(new)) > current + [0] * (width - len(current))


def latest_release() -> str:
    """The latest published Release's tag: GitHub leaves drafts and pre-releases out."""
    request = urllib.request.Request(
        LATEST_RELEASE,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "aldi"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return str(json.load(response)["tag_name"])


def installed_version() -> str:
    return importlib.metadata.version("aldi")


def installed_from_release() -> bool:
    """Whether this is Aldi's `uv` tool environment, as the install line made it, rather than a checkout."""
    return (Path(sys.prefix) / "uv-receipt.toml").is_file()


class Reinstaller(Protocol):
    def prepare(self, tag: str) -> None:
        """Downloads and builds the Release `tag` beside the running app; raises when it cannot."""
        ...

    def restart(self, tag: str) -> None:
        """Called as the app exits: reinstalls Aldi at `tag` once it has, then opens it again."""
        ...


class Updates:
    """Whether a newer Release is out, asked once per launch, and the update to it."""

    def __init__(
        self,
        data_dir: Path,
        version: str,
        latest_release: Callable[[], str] | None,
        reinstaller: Reinstaller | None,
        reporter: Reporter,
    ):
        """`latest_release` None means this is no install from a Release, as in development: nothing is offered."""
        self._failed = data_dir / "update" / "failed.json"
        self._version = version
        self._latest_release = latest_release
        self._reinstaller = reinstaller
        self._reporter = reporter
        self._lock = threading.Lock()
        self._available: str | None = None
        self._ready = ""
        self.notice = ""
        """The Spanish line the UI shows when an update failed."""

    def available(self) -> str:
        """The newer Release's tag, or empty."""
        with self._lock:
            if self._available is None:
                self._report_failed_reinstall()
                self._available = self._check()
            return self._available

    def update(self) -> bool:
        """Prepares the newer Release for `finish`; False, with an Error Report filed or queued, when it fails."""
        tag = self.available()
        if not tag or self._reinstaller is None:
            raise LookupError(NOTHING_NEWER)
        try:
            self._reinstaller.prepare(tag)
        except Exception as error:
            self.notice = _failed(self._reporter.report("Update failed", describe(error), None))
            return False
        self._ready = tag
        return True

    def finish(self) -> None:
        """Called once the app has closed: reinstalls the prepared Release, if any."""
        if self._ready and self._reinstaller is not None:
            self._reinstaller.restart(self._ready)

    def _check(self) -> str:
        if self._latest_release is None:
            return ""
        try:
            tag = self._latest_release()
        except Exception:
            # No connection or GitHub down: she keeps this version, and the next launch asks again.
            return ""
        return tag if is_newer(tag, self._version) else ""

    def _report_failed_reinstall(self) -> None:
        """The reinstall runs after the app exits, so the launch after it files its Error Report."""
        if not self._failed.is_file():
            return
        failed = json.loads(self._failed.read_text(encoding="utf-8"))
        self._failed.unlink()
        restored = "yes" if failed.get("restored") else "no"
        tag = str(failed.get("tag", ""))
        details = ErrorDetails(
            trace=f"uv tool install of {tag} failed; previous version restored: {restored}",
            fingerprint="update-reinstall",
            gemini_error="",
            summary=f"reinstalling {tag}",
        )
        self.notice = _failed(self._reporter.report("Update failed", details, None))


class UvReinstaller:
    """Reinstalls Aldi as a `uv` tool at a Release tag, the way the install line installed it."""

    def __init__(self, data_dir: Path, version: str, uv: Path | None = None, python: Path | None = None):
        """`python` is the interpreter the app runs on, in the tool environment: the relaunch starts it again."""
        self._folder = data_dir / "update"
        self._version = version
        self._uv = uv
        self._python = python or Path(sys.executable)
        self._helper: subprocess.Popen | None = None

    def plan(self, tag: str) -> dict:
        """The commands `aldi.reinstall` runs: install `tag`, or put this version back, then open Aldi."""
        uv = str(self._uv or _find_uv())
        current = f"v{self._version}"
        return {
            "tag": tag,
            "install": [uv, "tool", "install", "--force", *self._options(tag), _spec(tag)],
            "fallback": [uv, "tool", "install", "--force", *self._options(current), _spec(current)],
            "relaunch": [str(self._python), "-m", "aldi"],
            "failed": str(self._folder / "failed.json"),
        }

    def prepare(self, tag: str) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        _download(_constraints_url(tag), self._constraints(tag))
        try:
            _download(_constraints_url(f"v{self._version}"), self._constraints(f"v{self._version}"))
        except OSError:
            pass  # Only the fallback needs it, and it is cached from the last update, if any.
        # Builds the Release in uv's cache, which the reinstall then takes it from, and checks that it starts.
        uv = str(self._uv or _find_uv())
        command = [uv, "tool", "run", *self._options(tag), "--from", _spec(tag), "aldi", "--help"]
        subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=_PREPARE_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def restart(self, tag: str) -> None:
        plan = self._folder / "plan.json"
        plan.write_text(json.dumps(self.plan(tag)), encoding="utf-8")
        script = self._folder / "reinstall.py"
        script.write_bytes(resources.files("aldi").joinpath("reinstall.py").read_bytes())
        python = Path(getattr(sys, "_base_executable", "") or sys.executable)
        if sys.platform == "win32":
            # python.exe rather than pythonw.exe, which may give it no stdin to wait on; detached, so no console.
            python = python.with_name("python.exe")
            options = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            options = {"start_new_session": True}
        # Kept, so its stdin stays open until this process exits.
        self._helper = subprocess.Popen(
            [str(python), "-I", str(script), str(plan)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **options,
        )

    def _options(self, tag: str) -> list[str]:
        return ["--managed-python", "--python", "3.12", "--constraints", str(self._constraints(tag))]

    def _constraints(self, tag: str) -> Path:
        return self._folder / f"constraints-{tag}.txt"


def _failed(notice: str) -> str:
    return f"{_FAILED} {notice.removeprefix('Algo salió mal. ')}"


def _spec(tag: str) -> str:
    # refs/tags/: a branch of the same name can never stand in for the tag.
    return f"aldi @ https://github.com/{REPO}/archive/refs/tags/{tag}.tar.gz"


def _constraints_url(tag: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/refs/tags/{tag}/install-constraints.txt"


def _download(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "aldi"})
    with urllib.request.urlopen(request, timeout=60) as response:
        path.write_bytes(response.read())


def _find_uv() -> Path:
    """The `uv` the install line used: on the PATH, or where it installs `uv` when there was none."""
    found = shutil.which("uv")
    if found:
        return Path(found)
    uv = Path.home() / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
    if not uv.is_file():
        raise FileNotFoundError("uv")
    return uv
