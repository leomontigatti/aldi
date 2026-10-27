"""The launcher opens the app in its own window, and closing the window stops the app (#16)."""

import urllib.error
import urllib.request

import pytest

from aldi.app import main


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
    assert "<title>Aldi</title>" in page
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(url, timeout=2)
