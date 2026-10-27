"""The app's state files are written whole or not at all (close-out of #3)."""

import pytest

from aldi import files
from aldi.settings import Settings
from conftest import AppClosed


def test_a_write_cut_off_midway_leaves_the_old_settings_readable(tmp_path, monkeypatch):
    # Bug: settings.json was written in place, so a crash mid-write left a file the app could not start with.
    settings = Settings(tmp_path)
    settings.save_key("AIza-valida-1")

    def cut_off(_descriptor):
        raise AppClosed

    monkeypatch.setattr(files.os, "fsync", cut_off)
    with pytest.raises(AppClosed):
        settings.save_key("AIza-valida-2")

    assert Settings(tmp_path).key == "AIza-valida-1"
    assert [path.name for path in tmp_path.iterdir()] == ["settings.json"]
