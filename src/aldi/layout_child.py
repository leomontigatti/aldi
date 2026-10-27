"""BabelDOC's side of the Layout-kept Translation, run in a child process of its own by `aldi.layout`.

BabelDOC keeps 1-2 GB after every run in the process it ran in; a child process gives all of it back to the system
when it exits. Its prompts go to the app over a pipe, so every Gemini request stays in the app's Translator.
"""

import threading
import traceback
from multiprocessing.connection import Connection
from pathlib import Path

from babeldoc.format.pdf import high_level
from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
from babeldoc.translator.translator import BaseTranslator


def run(source: Path, pages: list[int], folder: Path, app: Connection) -> None:
    """Writes the Spanish of `pages` of `source` over the original pages in `folder`, asking the app for each answer.

    Sends `("prompt", text)` and waits for `("answer", text)`; ends with `("done", path of the PDF)` or
    `("error", traceback)`.
    """
    try:
        config = TranslationConfig(
            translator=_Translator(app),
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
            # Aldi offers this mode only for pages with a text layer; BabelDOC's own guess refuses pages with little
            # text.
            skip_scanned_detection=True,
            # One request at a time: flash's free tier takes 5 a minute (ADR 1).
            qps=1,
            pool_max_workers=1,
            use_rich_pbar=False,
        )
        result = high_level.translate(config)
        app.send(("done", str(result.mono_pdf_path)))
    except Exception:
        app.send(("error", traceback.format_exc()))
    finally:
        app.close()


class _Translator(BaseTranslator):
    """BabelDOC's translator, each prompt answered by the app. The app ends this process when an answer fails, so
    BabelDOC never sees an error it would answer by leaving the paragraph in English."""

    # BabelDOC's own cache keys on this name; the app's kept answers are the cache that counts.
    name = "aldi"

    def __init__(self, app: Connection):
        super().__init__("en", "es", ignore_cache=True)
        self._app = app
        self._lock = threading.Lock()

    def do_llm_translate(self, text, rate_limit_params: dict | None = None):
        if text is None:
            # BabelDOC's check that this translator takes whole prompts.
            return None
        with self._lock:
            self._app.send(("prompt", text))
            _kind, answer = self._app.recv()
        return answer

    def do_translate(self, text, rate_limit_params: dict | None = None):
        raise NotImplementedError
