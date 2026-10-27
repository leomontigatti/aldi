"""The Layout-kept Translation: BabelDOC, `pdf2zh-next`'s engine, writes the Spanish over the original pages.

BabelDOC runs in a child process (`aldi.layout_child`) with a translator of ours in place of `pdf2zh-next`'s
engines: it sends each prompt here, where `Translator.answer_layout_prompt` answers it like every other Gemini
request of the app. The child exits after each run, and the memory BabelDOC keeps goes with it.
"""

import hashlib
import json
import multiprocessing
import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

# How often the app looks at whether it is closing while BabelDOC works, in seconds.
_POLL = 0.1
# How long a child that said it is done gets to exit before it is ended.
_EXIT = 30


def fetch_assets() -> None:
    """Downloads what BabelDOC needs to run, about 345 MB (fonts, its layout model, tiktoken's encoding), into
    `~/.cache/babeldoc`; files already there and intact are kept. `aldi setup` runs it, so a Job never waits on it."""
    from babeldoc.assets.assets import warmup

    warmup()


class Cancelled(Exception):
    """The app is closing: the Layout-kept Translation stops, its answers kept for the next run."""


class LayoutFailed(RuntimeError):
    """BabelDOC failed, or its process ended without a result."""


def write_layout_kept(
    source: Path,
    pages: Sequence[int],
    folder: Path,
    answer: Callable[[str], str],
    closing: Callable[[], bool],
) -> Path:
    """Writes the Spanish of `pages` of `source` over the original pages, and only those pages, in `folder`.

    Each of Gemini's answers is kept in `folder` as it arrives, so a run stopped by a Quota Pause, a lost connection
    or the app closing asks for none of them again. Raises what `answer` raised, the first time it raised, rather
    than write a PDF with paragraphs left in English; `Cancelled` as soon as `closing` holds, even while BabelDOC is
    still laying the pages out; `LayoutFailed` when BabelDOC fails.
    """
    kept = _KeptAnswers(folder / "answers.jsonl", answer)
    # `spawn`, as on Windows and macOS: a fresh interpreter, not a copy of the app's threads and memory.
    context = multiprocessing.get_context("spawn")
    ours, theirs = context.Pipe()
    child = context.Process(target=_run_child, args=(source, list(pages), folder, theirs), name="aldi-babeldoc")
    child.start()
    theirs.close()
    done = False
    try:
        while True:
            if closing():
                raise Cancelled
            if not ours.poll(_POLL):
                if not child.is_alive() and not ours.poll():
                    raise LayoutFailed(f"BabelDOC's process ended with code {child.exitcode}")
                continue
            try:
                kind, value = ours.recv()
            except EOFError as error:
                raise LayoutFailed(f"BabelDOC's process ended with code {child.exitcode}") from error
            if kind == "prompt":
                ours.send(("answer", kept(value)))
            elif kind == "done":
                done = True
                return Path(value)
            else:
                raise LayoutFailed(f"BabelDOC failed in its process:\n{value}")
    finally:
        if done:
            child.join(_EXIT)
        if child.is_alive():
            child.kill()
        child.join()
        ours.close()


def _run_child(*args) -> None:
    threading.Thread(target=_end_with_the_app, daemon=True, name="aldi-app-watch").start()
    # Imported in the child only: the app's own process never loads BabelDOC.
    from aldi import layout_child

    layout_child.run(*args)


def _end_with_the_app() -> None:
    """Ends BabelDOC's process as soon as the app's is gone, killed or crashed: BabelDOC reads the pipe only when it
    has a prompt, and lays pages out for minutes before its first."""
    multiprocessing.parent_process().join()
    os._exit(1)


class _KeptAnswers:
    """Gemini's answer to each prompt, asked once: the answers so far are on disk, one JSON line each."""

    def __init__(self, path: Path, answer: Callable[[str], str]):
        self._path = path
        self._answer = answer
        self._lock = threading.Lock()
        self._kept: dict[str, str] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                # A line cut short by the app's process ending is asked again.
                try:
                    kept = json.loads(line)
                except ValueError:
                    continue
                self._kept[kept["prompt"]] = kept["answer"]

    def __call__(self, prompt: str) -> str:
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        with self._lock:
            if key in self._kept:
                return self._kept[key]
        answer = self._answer(prompt)
        with self._lock:
            self._kept[key] = answer
            with self._path.open("a", encoding="utf-8") as kept:
                kept.write(json.dumps({"prompt": key, "answer": answer}, ensure_ascii=False) + "\n")
        return answer
