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


def refusing_rename(times):
    """`os.replace` refusing the first `times` renames as Windows does while another handle has the file open."""
    calls = []

    def replace(source, target):
        calls.append(target)
        if len(calls) <= times:
            raise PermissionError(13, "The process cannot access the file because it is being used")
        real_replace(source, target)

    return replace, calls


real_replace = files.os.replace


def test_a_settings_file_being_read_while_it_is_saved_is_saved_once_the_reader_lets_go(tmp_path, monkeypatch):
    # Bug: on Windows a rename over a file another handle has open fails at once, so saving the key failed.
    replace, calls = refusing_rename(2)
    monkeypatch.setattr(files.os, "replace", replace)

    Settings(tmp_path).save_key("AIza-valida-1")

    assert Settings(tmp_path).key == "AIza-valida-1"
    assert len(calls) == 3
    assert [path.name for path in tmp_path.iterdir()] == ["settings.json"]


def test_a_rename_refused_every_time_fails_and_leaves_no_temporary_file(tmp_path, monkeypatch):
    replace, _calls = refusing_rename(100)
    monkeypatch.setattr(files.os, "replace", replace)

    with pytest.raises(PermissionError):
        files.write_atomically(tmp_path / "job.json", "{}")

    assert list(tmp_path.iterdir()) == []
