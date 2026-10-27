"""What the install line does once `uv` has installed Aldi: the reporting token and the launcher (#16)."""

import getpass
import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from aldi.install import create_launcher, setup
from aldi.settings import Settings
from conftest import REPORT_TOKEN, FakeGitHub


def test_setup_saves_the_reporting_token_and_creates_the_launcher(tmp_path):
    settings = Settings(tmp_path / "config")
    github = FakeGitHub()
    made = []

    done = setup(f"  {REPORT_TOKEN}\n", settings, tmp_path / "data", github, lambda: made.append("launcher") or [])

    assert done
    assert made == ["launcher"]
    assert Settings(tmp_path / "config").report_token == REPORT_TOKEN
    assert github.issues == []


def test_a_failed_install_files_an_error_report_with_nothing_private(tmp_path):
    settings = Settings(tmp_path / "config")
    github = FakeGitHub()
    desktop = Path.home() / "Escritorio de Corrêa"

    def launcher():
        raise PermissionError(f"cannot write {desktop} for {getpass.getuser()} with {REPORT_TOKEN}")

    done = setup(REPORT_TOKEN, settings, tmp_path / "data", github, launcher)

    assert not done
    [report] = github.reports()
    assert report["title"].startswith("Error Report: Install failed: PermissionError")
    assert "aldi/install.py" in report["body"]
    assert "Output Mode" not in report["body"]
    published = report["title"] + report["body"]
    for private in [REPORT_TOKEN, "Corrêa", str(Path.home())]:
        assert private not in published
    assert set(github.tokens) == {REPORT_TOKEN}


def test_a_failed_install_with_no_connection_keeps_the_report_for_the_app_to_send(tmp_path):
    github = FakeGitHub()
    github.offline = True

    def launcher():
        raise OSError("disk full")

    assert not setup(REPORT_TOKEN, Settings(tmp_path / "config"), tmp_path / "data", github, launcher)

    assert github.issues == []
    assert len(list((tmp_path / "data" / "reports" / "queue").glob("*.json"))) == 1


def test_the_macos_launcher_is_an_app_that_starts_aldi_with_its_own_python(tmp_path):
    applications = tmp_path / "Applications"
    python = tmp_path / "uv tools" / "aldi" / "bin" / "python"

    [app] = create_launcher(python, tmp_path / "data", "Darwin", [applications])

    assert app == applications / "Aldi.app"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "Aldi"
    assert info["CFBundlePackageType"] == "APPL"
    executable = app / "Contents" / "MacOS" / info["CFBundleExecutable"]
    assert (app / "Contents" / "Resources" / info["CFBundleIconFile"]).read_bytes().startswith(b"icns")
    if sys.platform == "win32":
        return
    assert executable.stat().st_mode & stat.S_IXUSR
    # Run it with a stand-in for the tool's Python that records how it was called.
    called = tmp_path / "called.txt"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{called}"\n', encoding="utf-8")
    python.chmod(0o755)
    subprocess.run([executable], check=True, timeout=10)
    assert called.read_text(encoding="utf-8").split() == ["-m", "aldi"]


def test_creating_the_macos_launcher_again_replaces_it(tmp_path):
    applications = tmp_path / "Applications"
    create_launcher(tmp_path / "old" / "python", tmp_path / "data", "Darwin", [applications])

    [app] = create_launcher(tmp_path / "new" / "python", tmp_path / "data", "Darwin", [applications])

    script = (app / "Contents" / "MacOS" / "Aldi").read_text(encoding="utf-8")
    assert "new" in script
    assert "old" not in script


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts are made through Windows' own shell")
def test_the_windows_launcher_is_a_shortcut_on_the_desktop_and_in_the_start_menu(tmp_path):
    desktop, start_menu = tmp_path / "Escritorio de Corrêa", tmp_path / "Programas"
    desktop.mkdir()
    python = tmp_path / "tools" / "aldi" / "Scripts" / "python.exe"

    shortcuts = create_launcher(python, tmp_path / "data", "Windows", [desktop, start_menu])

    assert shortcuts == [desktop / "Aldi.lnk", start_menu / "Aldi.lnk"]
    for shortcut in shortcuts:
        read = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ALDI_LNK); "
            "$s.TargetPath; $s.Arguments; $s.IconLocation"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", read],
            env={**os.environ, "ALDI_LNK": str(shortcut)},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        assert out[0] == str(python.with_name("pythonw.exe"))
        assert out[1] == "-m aldi"
        assert out[2].startswith(str(tmp_path / "data" / "Aldi.ico"))
    assert (tmp_path / "data" / "Aldi.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
