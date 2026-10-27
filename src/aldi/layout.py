"""The Layout-kept Translation: BabelDOC, `pdf2zh-next`'s engine, writes the Spanish over the original pages.

BabelDOC runs in this process with a translator of ours in place of `pdf2zh-next`'s engines, so its requests go
through `Translator.answer_layout_prompt` like every other Gemini request of the app.
"""

import hashlib
import json
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from babeldoc.format.pdf import high_level
from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
from babeldoc.translator.translator import BaseTranslator

from aldi.translator import Offline, QuotaExhausted


class Cancelled(Exception):
    """The app is closing: the Layout-kept Translation stops, its answers kept for the next run."""


def write_layout_kept(
    source: Path,
    pages: Sequence[int],
    folder: Path,
    answer: Callable[[str], str],
    closing: Callable[[], bool],
) -> Path:
    """Writes the Spanish of `pages` of `source` over the original pages, and only those pages, in `folder`.

    Each of Gemini's answers is kept in `folder` as it arrives, so a run stopped by a Quota Pause, a lost connection
    or the app closing asks for none of them again. Raises `QuotaExhausted`, `Offline` or `Cancelled` when one
    stopped it.
    """
    translator = _Translator(_KeptAnswers(folder / "answers.jsonl", answer), closing)
    config = TranslationConfig(
        translator=translator,
        input_file=source,
        lang_in="en",
        lang_out="es",
        doc_layout_model=None,
        pages=",".join(str(number) for number in pages),
        output_dir=folder,
        working_dir=folder / "work",
        no_dual=True,
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
        only_include_translated_page=True,
        auto_extract_glossary=False,
        primary_font_family="serif",
        # One request at a time: flash's free tier takes 5 a minute (ADR 1).
        qps=1,
        pool_max_workers=1,
        use_rich_pbar=False,
    )
    result = high_level.translate(config)
    if translator.stopped:
        raise translator.stopped
    return Path(result.mono_pdf_path)


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


class _Translator(BaseTranslator):
    """BabelDOC's translator, answering its prompts through Aldi's. A Quota Pause, a lost connection or the app
    closing stops every request after it: BabelDOC would otherwise leave those paragraphs in English and carry on."""

    # BabelDOC's own cache keys on this name; `_KeptAnswers` is the cache that counts.
    name = "aldi"

    def __init__(self, answer: Callable[[str], str], closing: Callable[[], bool]):
        super().__init__("en", "es", ignore_cache=True)
        self._answer = answer
        self._closing = closing
        self.stopped: Exception | None = None

    def do_llm_translate(self, text, rate_limit_params: dict | None = None):
        if text is None:
            # BabelDOC's check that this translator takes whole prompts.
            return None
        if not self.stopped and self._closing():
            self.stopped = Cancelled()
        if self.stopped:
            raise self.stopped
        try:
            return self._answer(text)
        except (QuotaExhausted, Offline) as error:
            self.stopped = error
            raise

    def do_translate(self, text, rate_limit_params: dict | None = None):
        raise NotImplementedError
