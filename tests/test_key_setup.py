"""First-run key setup and the settings that change the key later (#14)."""

import json
import sys
import urllib.error
from pathlib import Path

import pytest

from aldi.settings import Settings, default_config_dir
from conftest import VALID_KEY, build_born_digital_pdf


@pytest.fixture
def settings(tmp_path):
    """First launch: nothing saved yet."""
    return Settings(tmp_path / "config")


def refused(call) -> tuple[int, str]:
    with pytest.raises(urllib.error.HTTPError) as error:
        call()
    return error.value.code, json.load(error.value)["error"]


def test_with_no_key_the_app_asks_for_one_and_starts_no_job(app, tmp_path):
    assert app.get("/api/settings") == {"has_key": False}

    source = build_born_digital_pdf(tmp_path / "Pulpa.pdf", [["The pulp is soft tissue."]])
    code, message = refused(lambda: app.upload(source))

    assert code == 409
    assert "clave" in message
    assert app.get("/api/jobs")["jobs"] == []


def test_an_invalid_key_is_refused_in_spanish_and_not_saved(app, settings, key_check):
    code, message = refused(lambda: app.send("PUT", "/api/settings", {"key": "AIza-mal-copiada"}))

    assert code == 400
    assert "clave" in message
    assert key_check.checked == ["AIza-mal-copiada"]
    assert app.get("/api/settings") == {"has_key": False}
    assert settings.key == ""


def test_an_empty_key_is_refused_without_calling_gemini(app, key_check):
    code, _ = refused(lambda: app.send("PUT", "/api/settings", {"key": "   "}))

    assert code == 400
    assert key_check.checked == []


def test_a_valid_key_is_saved_and_the_app_translates_with_it(app, key_check, translator_keys, tmp_path):
    assert app.send("PUT", "/api/settings", {"key": f"  {VALID_KEY}\n"}) == {"has_key": True}

    assert key_check.checked == [VALID_KEY]
    assert Settings(tmp_path / "config").key == VALID_KEY
    source = build_born_digital_pdf(tmp_path / "Pulpa.pdf", [["The pulp is soft tissue."]])
    job = app.wait_for(app.upload(source)["id"])
    assert job["state"] == "done"
    assert translator_keys == [VALID_KEY]
    job_files = [path for path in (tmp_path / "data" / "jobs" / job["id"]).rglob("*") if path.is_file()]
    assert not [path for path in job_files if VALID_KEY.encode() in path.read_bytes()]


def test_a_key_changed_in_the_settings_is_used_by_the_next_job(app, key_check, translator_keys, tmp_path):
    app.send("PUT", "/api/settings", {"key": VALID_KEY})
    source = build_born_digital_pdf(tmp_path / "Pulpa.pdf", [["The pulp is soft tissue."]])
    app.wait_for(app.upload(source)["id"])

    app.send("PUT", "/api/settings", {"key": "AIza-valida-2"})
    app.wait_for(app.upload(source)["id"])

    assert translator_keys == [VALID_KEY, "AIza-valida-2"]


def test_development_reads_the_key_from_env_when_none_is_saved(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('# a comment\nGEMINI_API_KEY="AIza-desde-env"\n', encoding="utf-8")

    assert Settings(tmp_path / "config", dev_env_file=env_file).key == "AIza-desde-env"
    assert Settings(tmp_path / "config").key == ""

    saved = Settings(tmp_path / "config", dev_env_file=env_file)
    saved.save_key("AIza-guardada")
    assert saved.key == "AIza-guardada"


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="her laptop runs Windows or macOS")
def test_the_settings_file_lives_in_the_user_config_directory():
    if sys.platform == "win32":
        assert default_config_dir() == Path.home() / "AppData" / "Local" / "Aldi"
    else:
        assert default_config_dir() == Path.home() / "Library" / "Application Support" / "Aldi"


def test_a_key_that_cannot_be_checked_is_not_saved(app, key_check):
    key_check.error = OSError("no connection")

    code, message = refused(lambda: app.send("PUT", "/api/settings", {"key": VALID_KEY}))

    assert code == 503
    assert "conexión" in message
    assert app.get("/api/settings") == {"has_key": False}
