"""Updates from Releases: a newer Release tag offers `Actualizar`, which reinstalls Aldi at it and restarts (#17)."""

import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from aldi import reinstall
from aldi.update import UvReinstaller, is_newer
from conftest import REPORT_TOKEN, VALID_KEY, build_born_digital_pdf, wait_until

UPDATE_FAILED = "No se pudo actualizar Aldi. Ya se envió un informe para que lo arreglen."


def post_update(app):
    try:
        return 200, app.send("POST", "/api/update")
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_a_newer_release_offers_the_update(app, github):
    github.release = "v0.2.0"

    assert app.get("/api/update") == {"available": "v0.2.0", "notice": ""}


def test_the_release_she_runs_offers_nothing(app, github):
    assert app.get("/api/update") == {"available": "", "notice": ""}


@pytest.mark.parametrize("tag", ["v0.0.9", "main", "v0.2.0-rc1", "0.2.0/../main", ""])
def test_only_a_newer_version_tag_counts(start_app, translator, github, tag):
    github.release = tag

    assert start_app(translator).get("/api/update")["available"] == ""


def test_the_app_asks_github_once_per_launch(app, github):
    github.release = "v0.2.0"

    app.get("/api/update")
    app.get("/api/update")

    assert github.release_checks == 1


def test_no_connection_offers_nothing_and_files_no_report(app, github):
    github.offline = True

    assert app.get("/api/update") == {"available": "", "notice": ""}
    assert github.reports() == []


def test_an_app_not_installed_from_a_release_never_asks(start_app, translator, github, tmp_path, settings):
    from aldi.app import create_server

    server = create_server(tmp_path / "dev", tmp_path / "docs", settings, lambda key: translator, bool, github)
    try:
        assert server.updates.available() == ""
    finally:
        server.server_close()
    assert github.release_checks == 0


def test_updating_prepares_the_release_closes_the_window_and_restarts_into_it(app, github, reinstaller):
    github.release = "v0.2.0"
    closed = []
    app.server.close_window = lambda: closed.append(True)
    app.get("/api/update")

    status, answer = post_update(app)

    assert status == 200
    assert answer == {"restarting": True}
    assert reinstaller.prepared == ["v0.2.0"]
    wait_until(lambda: closed == [True])
    assert reinstaller.restarted == []
    app.stop()
    app.server.updates.finish()
    assert reinstaller.restarted == ["v0.2.0"]


def test_closing_the_window_without_updating_restarts_nothing(app, github, reinstaller):
    github.release = "v0.2.0"
    app.get("/api/update")
    app.stop()

    app.server.updates.finish()

    assert reinstaller.restarted == []


def test_updating_with_nothing_newer_is_refused(app, reinstaller):
    status, answer = post_update(app)

    assert status == 409
    assert answer["error"] == "Ya tienes la última versión."
    assert reinstaller.prepared == []


def test_updating_keeps_her_settings_jobs_and_results(start_app, translator, github, settings, tmp_path):
    github.release = "v0.2.0"
    app = start_app(translator)
    job = app.wait_for(app.upload(build_born_digital_pdf(tmp_path / "Caries.pdf", [["The pulp."]]))["id"])
    app.get("/api/update")
    assert post_update(app)[0] == 200
    app.stop()
    app.server.updates.finish()

    after = start_app(translator)

    assert [kept["id"] for kept in after.get("/api/jobs")["jobs"]] == [job["id"]]
    assert all((Path(job["folder"]) / name).is_file() for name in job["results"])
    assert (settings.key, settings.report_token) == (VALID_KEY, REPORT_TOKEN)


def test_a_failed_update_keeps_this_version_running_and_reports(app, github, reinstaller):
    github.release = "v0.2.0"
    reinstaller.error = subprocess.CalledProcessError(2, ["uv", "tool", "run"])
    closed = []
    app.server.close_window = lambda: closed.append(True)
    app.get("/api/update")

    status, answer = post_update(app)

    assert status == 502
    assert answer["error"] == UPDATE_FAILED
    assert closed == []
    assert app.get("/api/update") == {"available": "v0.2.0", "notice": UPDATE_FAILED}
    assert app.get("/api/jobs") == {"jobs": []}
    [report] = github.reports()
    assert report["title"].startswith("Error Report: Update failed: CalledProcessError")
    assert "aldi/update.py" in report["body"]
    app.stop()
    app.server.updates.finish()
    assert reinstaller.restarted == []


def test_a_reinstall_that_failed_after_closing_is_reported_on_the_next_launch(start_app, translator, github, tmp_path):
    failed = tmp_path / "data" / "update" / "failed.json"
    failed.parent.mkdir(parents=True)
    failed.write_text(json.dumps({"tag": "v0.2.0", "restored": True}), encoding="utf-8")

    app = start_app(translator)

    assert app.get("/api/update")["notice"] == UPDATE_FAILED
    [report] = github.reports()
    assert report["title"] == "Error Report: Update failed: reinstalling v0.2.0"
    assert "previous version restored: yes" in report["body"]
    assert not failed.exists()


@pytest.mark.parametrize(
    ("tag", "version", "newer"),
    [
        ("v0.2.0", "0.1.0", True),
        ("v0.10.0", "0.9.0", True),
        ("v1.0", "0.9.9", True),
        ("v0.1.0", "0.1.0", False),
        ("v0.1.0", "0.2.0", False),
        ("0.2.0", "0.1.0", False),
        ("v0.2.0-beta", "0.1.0", False),
    ],
)
def test_a_release_is_newer_only_by_its_version_tag(tag, version, newer):
    assert is_newer(tag, version) is newer


def test_the_reinstall_installs_the_release_tag_and_falls_back_to_this_version(tmp_path):
    uv = tmp_path / "uv"
    python = tmp_path / "tool" / "bin" / "python"

    plan = UvReinstaller(tmp_path / "data", "0.1.0", uv=uv, python=python).plan("v0.2.0")

    tags = "https://github.com/leomontigatti/aldi/archive/refs/tags"
    assert plan["install"][:3] == [str(uv), "tool", "install"]
    assert "--force" in plan["install"]
    assert plan["install"][-1] == f"aldi @ {tags}/v0.2.0.tar.gz"
    assert plan["fallback"][-1] == f"aldi @ {tags}/v0.1.0.tar.gz"
    constraints = plan["install"][plan["install"].index("--constraints") + 1]
    assert Path(constraints).parent == tmp_path / "data" / "update"
    assert plan["relaunch"] == [str(python), "-m", "aldi"]
    assert plan["failed"] == str(tmp_path / "data" / "update" / "failed.json")


class Commands:
    def __init__(self, exit_codes):
        self.exit_codes = list(exit_codes)
        self.run: list[list[str]] = []
        self.spawned: list[list[str]] = []
        self.waited = 0

    def wait(self):
        self.waited += 1

    def call(self, command):
        assert self.waited, "uv ran before the app exited"
        self.run.append(command)
        return self.exit_codes.pop(0)

    def spawn(self, command):
        self.spawned.append(command)


def plan(tmp_path):
    return {
        "tag": "v0.2.0",
        "install": ["uv", "install", "v0.2.0"],
        "fallback": ["uv", "install", "v0.1.0"],
        "relaunch": ["python", "-m", "aldi"],
        "failed": str(tmp_path / "failed.json"),
    }


def test_the_helper_waits_for_the_app_to_exit_then_installs_and_reopens_it(tmp_path):
    commands = Commands([0])

    assert reinstall.run(plan(tmp_path), commands.wait, commands.call, commands.spawn, sleep=lambda _: None)

    assert commands.run == [["uv", "install", "v0.2.0"]]
    assert commands.spawned == [["python", "-m", "aldi"]]
    assert not (tmp_path / "failed.json").exists()


def test_the_helper_retries_while_windows_still_holds_the_old_files(tmp_path):
    commands = Commands([1, 0])

    assert reinstall.run(plan(tmp_path), commands.wait, commands.call, commands.spawn, sleep=lambda _: None)

    assert commands.run == [["uv", "install", "v0.2.0"]] * 2


def test_a_failed_reinstall_puts_this_version_back_reopens_it_and_leaves_the_failure(tmp_path):
    commands = Commands([1, 1, 1, 0])

    assert not reinstall.run(plan(tmp_path), commands.wait, commands.call, commands.spawn, sleep=lambda _: None)

    assert commands.run == [["uv", "install", "v0.2.0"]] * 3 + [["uv", "install", "v0.1.0"]]
    assert commands.spawned == [["python", "-m", "aldi"]]
    failed = json.loads((tmp_path / "failed.json").read_text(encoding="utf-8"))
    assert failed == {"tag": "v0.2.0", "restored": True}


def test_the_helper_runs_on_the_standard_library_alone(tmp_path):
    """The app runs it with the base Python, outside the environment being replaced, so it cannot import Aldi."""
    script = Path(reinstall.__file__)
    source = script.read_text(encoding="utf-8")

    assert "import aldi" not in source and "from aldi" not in source
    done = subprocess.run([sys.executable, "-I", str(script), "--help"], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
