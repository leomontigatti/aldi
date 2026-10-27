"""The reading pipeline: a Source Document's page turned into Spanish blocks."""

from dataclasses import replace

from aldi.blocks import TRANSLATED, Block, Kind, has_emphasis, without_emphasis
from aldi.source import SourcePage
from aldi.translator import Translator
from aldi.writer import TranslatedPage


def translate_page(
    page: SourcePage, translator: Translator, terms: dict[str, str]
) -> tuple[TranslatedPage, dict[str, str]]:
    """The page with its text in Spanish, and the Term List after it. References and Figures stay as they are."""
    texts = [text for block in page.blocks if block.kind in TRANSLATED for text in _texts(block) if text]
    if not texts:
        return TranslatedPage(page.number, page.blocks), terms
    answer = translator.translate_texts(texts, terms)
    queue = iter(zip(texts, answer.texts, strict=True))

    def translated(text: str) -> str:
        if not text:
            return text
        original, result = next(queue)
        # Emphasis comes from the original's fonts only; a translator that adds some is not believed.
        return result if has_emphasis(original) else without_emphasis(result)

    blocks = []
    for block in page.blocks:
        if block.kind == Kind.TABLE:
            block = replace(block, rows=[[translated(cell) for cell in row] for row in block.rows])
        elif block.kind in TRANSLATED:
            block = replace(block, text=translated(block.text))
        blocks.append(block)
    return TranslatedPage(page.number, blocks), answer.terms


def _texts(block: Block) -> list[str]:
    return [cell for row in block.rows for cell in row] if block.kind == Kind.TABLE else [block.text]
