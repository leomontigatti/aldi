"""`aldi setup`, the install line's last step: saves the reporting token, creates the launcher and downloads what
Mantener el diseño needs.

`install.ps1` and `install.sh` install `uv` and Aldi, then run this with the token in `ALDI_REPORT_TOKEN`. The
launcher starts the Python of Aldi's `uv` tool environment, which an update reinstalls in place, so it outlives
updates.
"""

import getpass
import os
import platform
import plistlib
import shlex
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path

import platformdirs
import pymupdf

from aldi.reports import GitHub, Reporter, describe
from aldi.settings import Settings, default_config_dir

NAME = "Aldi"


def setup(
    token: str,
    settings: Settings,
    data_dir: Path,
    make_github: Callable[[str], GitHub],
    launcher: Callable[[], list],
    assets: Callable[[], object],
) -> bool:
    """Saves the token, creates the launcher and downloads the assets; False, with an Error Report filed or queued,
    when any of them fails."""
    token = token.strip()
    try:
        settings.save_report_token(token)
        launcher()
        assets()
        return True
    except Exception as error:
        reporter = Reporter(data_dir, lambda: token, make_github, secrets=lambda: [token])
        try:
            notice = reporter.report("Install failed", describe(error), None)
        finally:
            reporter.close()
        print(notice, file=sys.stderr)
        return False


def create_launcher(python: Path, data_dir: Path, system: str, folders: list[Path]) -> list[Path]:
    """What she opens Aldi from, in each of `folders`: a shortcut on Windows, an `.app` on macOS.

    `python` is the tool environment's interpreter; the icon is kept in `data_dir`, which an update leaves alone.
    """
    if system == "Windows":
        icon = data_dir / f"{NAME}.ico"
        icon.parent.mkdir(parents=True, exist_ok=True)
        icon.write_bytes(_ico(_icon_png()))
        return [_shortcut(folder / f"{NAME}.lnk", python.with_name("pythonw.exe"), icon) for folder in folders]
    if system == "Darwin":
        return [_app_bundle(folder / f"{NAME}.app", python) for folder in folders]
    return []


def launcher_folders(system: str) -> list[Path]:
    """Where her launchers go: the desktop and the Start menu on Windows, Applications on macOS."""
    if system == "Windows":
        programs = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        return [Path(platformdirs.user_desktop_dir()), programs]
    if system == "Darwin":
        applications = Path("/Applications")
        return [applications if os.access(applications, os.W_OK) else Path.home() / "Applications"]
    return []


def main() -> int:
    from aldi.reports import GitHubClient

    token = os.environ.get("ALDI_REPORT_TOKEN", "").strip()
    while not token:
        token = getpass.getpass("Token para los informes de errores: ").strip()
    system = platform.system()
    data_dir = Path(platformdirs.user_data_dir(NAME, appauthor=False))
    made: list[Path] = []

    def launcher() -> list[Path]:
        made.extend(create_launcher(Path(sys.executable), data_dir, system, launcher_folders(system)))
        return made

    def assets() -> None:
        # Here rather than on her first Layout-kept Job, which would otherwise wait on the download.
        from aldi.layout import fetch_assets

        print("Descargando lo que necesita Mantener el diseño (unos 350 MB)...")
        fetch_assets()

    if not setup(token, Settings(default_config_dir()), data_dir, GitHubClient, launcher, assets):
        print("No se pudo terminar de instalar Aldi.", file=sys.stderr)
        return 1
    for path in made:
        print(f"Acceso directo creado: {path}")
    print("Aldi quedó instalado.")
    return 0


def _shortcut(path: Path, target: Path, icon: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Through the environment, so no path is ever quoted into the command.
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ALDI_LNK); "
        "$s.TargetPath = $env:ALDI_TARGET; $s.Arguments = '-m aldi'; $s.WorkingDirectory = $env:USERPROFILE; "
        "$s.IconLocation = $env:ALDI_ICON; $s.Description = 'Aldi'; $s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "ALDI_LNK": str(path), "ALDI_TARGET": str(target), "ALDI_ICON": str(icon)},
        check=True,
        capture_output=True,
    )
    return path


def _app_bundle(app: Path, python: Path) -> Path:
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    info = {
        "CFBundleName": NAME,
        "CFBundleDisplayName": NAME,
        "CFBundleIdentifier": "io.github.leomontigatti.aldi",
        "CFBundleExecutable": NAME,
        "CFBundleIconFile": f"{NAME}.icns",
        "CFBundlePackageType": "APPL",
        "NSHighResolutionCapable": True,
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    (contents / "Resources" / f"{NAME}.icns").write_bytes(_icns(_icon_png()))
    executable = contents / "MacOS" / NAME
    executable.write_text(f"#!/bin/sh\nexec {shlex.quote(str(python))} -m aldi\n", encoding="utf-8", newline="\n")
    executable.chmod(0o755)
    return app


def _icon_png() -> bytes:
    """The app's icon, 256 pixels square."""
    svg = resources.files("aldi").joinpath("icon.svg").read_bytes()
    with pymupdf.open(stream=svg, filetype="svg") as document:
        page = document[0]
        zoom = 256 / page.rect.width
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True).tobytes("png")


def _ico(png: bytes) -> bytes:
    # One 256-pixel PNG entry; a 0 width or height means 256.
    return struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22) + png


def _icns(png: bytes) -> bytes:
    # `ic08` is the 256-pixel PNG entry.
    entry = b"ic08" + struct.pack(">I", 8 + len(png)) + png
    return b"icns" + struct.pack(">I", 8 + len(entry)) + entry
