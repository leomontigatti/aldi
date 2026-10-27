"""Where a Job's results land: `<Documents>/Aldi/<nombre>/`, never over a file already there."""

import re
import shutil
from pathlib import Path

# Characters Windows forbids in a file name, and control characters.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
# Keeps `<Documents>/Aldi/<nombre>/<nombre> (es, diseño) (99).docx` well under Windows' 260-character paths.
_MAX_NAME = 80


def document_name(filename: str) -> str:
    """The name her results carry: the Source Document's file name without `.pdf`, safe on Windows and macOS."""
    stem = Path(filename.replace("\\", "/")).name
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    name = " ".join(_FORBIDDEN.sub(" ", stem).split())[:_MAX_NAME].rstrip(". ")
    if not name or name.split(".")[0].upper() in _RESERVED:
        name = f"Documento {name}".strip()
    return name


def deliver(result: Path, documents_dir: Path, name: str, filename: str) -> Path:
    """Copies `result` to `<documents_dir>/Aldi/<name>/<filename>`, numbering it rather than overwriting."""
    folder = documents_dir / "Aldi" / name
    folder.mkdir(parents=True, exist_ok=True)
    target = Path(filename)
    for attempt in range(1, 1000):
        candidate = folder / (filename if attempt == 1 else f"{target.stem} ({attempt}){target.suffix}")
        try:
            # Exclusive creation: a file that appeared since the check is never overwritten.
            with candidate.open("xb") as out, result.open("rb") as source:
                shutil.copyfileobj(source, out)
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError(folder / filename)
