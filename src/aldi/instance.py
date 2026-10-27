"""One app per data folder: a second launch brings the running app's window to the front instead.

The running app holds an OS lock on `aldi.lock` in its data folder and writes its port to `aldi.port` beside it.
The system drops the lock when the process ends, however it ends, so a run that crashed never blocks the next.
"""

import sys
import time
import urllib.request
from pathlib import Path
from typing import IO

# How long a second launch waits for a running app that is still starting to say which port it serves.
_STARTING = 10


def hold(data_dir: Path) -> IO | None:
    """Takes the data folder's lock, kept until the process ends; None when a running app holds it."""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = (data_dir / "aldi.lock").open("a+b")
    try:
        if sys.platform == "win32":
            import msvcrt

            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    (data_dir / "aldi.port").unlink(missing_ok=True)
    return lock


def serving(data_dir: Path, port: int) -> None:
    """Records the port the app holding the lock serves, for a second launch to reach it."""
    (data_dir / "aldi.port").write_text(str(port), encoding="utf-8")


def bring_to_front(data_dir: Path) -> bool:
    """Asks the app that holds the lock to bring its window to the front; False when it does not answer."""
    deadline = time.monotonic() + _STARTING
    while time.monotonic() < deadline:
        try:
            port = int((data_dir / "aldi.port").read_text(encoding="utf-8"))
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/window/front", data=b"", method="POST")
            with urllib.request.urlopen(request, timeout=5):
                return True
        except (OSError, ValueError):
            time.sleep(0.1)
    return False
