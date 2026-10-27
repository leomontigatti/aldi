"""Reinstalls Aldi at a Release tag once the app has exited, then opens it again.

`aldi.update` copies this file out of Aldi's `uv` tool environment and runs it with the base Python, its stdin a
pipe the app holds open until it exits: the environment being replaced must not be in use, which on Windows
means no process runs from it. So nothing here imports Aldi; the standard library only.

When the install fails, this version is put back, and `failed.json` tells the next launch to file the Error
Report (ADR 4).
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

# Windows can hold an exited process's files for a moment.
_ATTEMPTS = 3
_PAUSE = 2.0


def run(
    plan: dict,
    wait_for_exit: Callable[[], None],
    call: Callable[[list[str]], int],
    spawn: Callable[[list[str]], None],
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Installs `plan["install"]`, or else `plan["fallback"]`, then opens `plan["relaunch"]`; True when updated."""
    wait_for_exit()
    updated = _install(plan["install"], call, sleep)
    if not updated:
        restored = _install(plan["fallback"], call, sleep)
        failed = {"tag": plan["tag"], "restored": restored}
        Path(plan["failed"]).write_text(json.dumps(failed), encoding="utf-8")
    spawn(plan["relaunch"])
    return updated


def _install(command: list[str], call: Callable[[list[str]], int], sleep: Callable[[float], None]) -> bool:
    for attempt in range(_ATTEMPTS):
        if attempt:
            sleep(_PAUSE)
        if call(command) == 0:
            return True
    return False


def _wait_for_exit() -> None:
    # The app holds the other end of stdin: it reads empty once the app's process has exited.
    while os.read(0, 4096):
        pass


def _call(command: list[str]) -> int:
    # No console window for uv on Windows; CREATE_NO_WINDOW is 0 elsewhere.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, creationflags=flags).returncode


def _spawn(command: list[str]) -> None:
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(command, creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL, close_fds=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reinstalls Aldi at a Release tag once the app has exited.")
    parser.add_argument("plan", type=Path, help="the plan aldi.update wrote, as JSON")
    plan = json.loads(parser.parse_args().plan.read_text(encoding="utf-8"))
    run(plan, _wait_for_exit, _call, _spawn)


if __name__ == "__main__":
    main()
