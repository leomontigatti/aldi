"""The launcher opens the app in its own window, and closing the window stops the app (#16)."""

import threading
import urllib.error
import urllib.request

import pytest

from aldi.app import main
from conftest import wait_until


def test_the_app_opens_in_its_window_and_stops_when_the_window_closes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    shown = []

    def window(url):
        """She looks at the window until she closes it: the page is there the whole time."""
        with urllib.request.urlopen(url, timeout=5) as response:
            shown.append((url, response.read().decode("utf-8")))

    dirs = ["--data-dir", "data", "--documents-dir", "Documentos", "--config-dir", "config"]
    main(dirs, open_window=window)

    [(url, page)] = shown
    assert url.startswith("http://127.0.0.1:")
    assert page.lstrip().lower().startswith("<!doctype html>")
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(url, timeout=2)


DIRS = ["--data-dir", "data", "--documents-dir", "Documentos", "--config-dir", "config"]


def test_a_second_launch_brings_the_running_app_to_the_front_instead_of_starting_another(tmp_path, monkeypatch):
    # Bug: a second launch started a second server on the same data folder, both running the same Jobs.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    shown, focused, closed = [], [], threading.Event()

    def window(url):
        shown.append(url)
        closed.wait(timeout=30)

    first = threading.Thread(
        target=main, args=(DIRS,), kwargs={"open_window": window, "focus_window": lambda: focused.append(1)}
    )
    first.start()
    try:
        wait_until(lambda: shown)

        main(DIRS, open_window=lambda url: shown.append(url), focus_window=lambda: None)

        assert len(shown) == 1
        assert len(focused) == 1
    finally:
        closed.set()
        first.join(timeout=30)


def test_a_lock_left_by_a_crashed_run_never_blocks_a_launch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # What a run killed mid-way leaves behind: its lock file, and the port nothing listens on any more.
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "aldi.lock").write_text("", encoding="utf-8")
    (tmp_path / "data" / "aldi.port").write_text("9", encoding="utf-8")
    shown = []

    main(DIRS, open_window=shown.append)

    assert len(shown) == 1
