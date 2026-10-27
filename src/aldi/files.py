"""Files the app keeps its state in, written so that a crash mid-write never leaves one half written."""

import os
import uuid
from pathlib import Path


def write_atomically(path: Path, text: str) -> None:
    """Writes `text` to `path` in UTF-8 through a temporary file beside it, renamed over it once complete: until
    then, the old file stays whole and readable."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
