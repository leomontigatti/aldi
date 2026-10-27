"""Files the app keeps its state in, written so that a crash mid-write never leaves one half written."""

import os
import time
import uuid
from pathlib import Path

# Windows refuses to rename over a file another handle has open (a read of it, an antivirus scan) for as long as it
# stays open: the rename is tried again after each of these waits, in seconds, before it fails.
_RENAME_WAITS = (0.05, 0.1, 0.2)


def write_atomically(path: Path, text: str) -> None:
    """Writes `text` to `path` in UTF-8 through a temporary file beside it, renamed over it once complete: until
    then, the old file stays whole and readable."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        for wait in (*_RENAME_WAITS, None):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if wait is None:
                    raise
                time.sleep(wait)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
