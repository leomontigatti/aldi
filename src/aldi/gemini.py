"""The real Translator: Gemini through `google-genai`, on her own free-tier key (ADR 1)."""

import json
from collections.abc import Mapping, Sequence

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from aldi.translator import PageBatchAnswer, TextTranslation

MODEL = "gemini-3.5-flash"

_TEXT_INSTRUCTION = """\
You translate a dentistry textbook or journal article from English or Portuguese into Spanish for a dentistry \
student. Use standard Spanish dental terminology.

You receive a JSON object with `blocks`, the text blocks of one page in reading order, and `terms`, the technical \
terms and acronyms already translated in this document with the Spanish chosen for each. Return `spanish`: \
exactly one Spanish translation per block, in the same order. A block marks bold as `**bold**` and italics as \
`*italics*`, and writes a literal `*` or `\\` as `\\*` or `\\\\`: put each mark around the Spanish words that \
translate the marked ones, keep every escape, and add no mark the block does not have. Translate every term in \
`terms` the way it says. Translate an acronym, with the original in parentheses the first time it appears. \
Return in `terms` only the technical terms and acronyms this page adds, each with the Spanish you chose. Return \
only the Spanish, never the original text."""


_BATCH_INSTRUCTION = """\
You read the scanned pages of a dentistry textbook or journal article in English or Portuguese and translate them \
into Spanish for a dentistry student. Use standard Spanish dental terminology.

You receive page images, each after a line `Page N` that gives its number, then a JSON object with `terms`, the \
technical terms and acronyms already translated in this document with the Spanish chosen for each. Return `pages`: \
exactly one entry per image, in the order sent, its `page` the N of its image, and in `blocks` that page's content \
in reading order (a page in two columns: all of the left column, then the right). Never move content from one \
page to another, even when a sentence continues on the next page. Each block has a `kind`:
- `heading`, `paragraph`, `list_item`, or `caption` (a figure's caption): `text` is its Spanish. Mark bold as \
`**bold**` and italics as `*italics*` where the page prints them, and write a literal `*` or `\\` as `\\*` or \
`\\\\`.
- `table`: `rows` is its cells' Spanish, row by row, the header row first, marked the same way.
- `figure`: a photo, drawing, radiograph or chart. `box_2d` is where it is on the page, \
[ymin, xmin, ymax, xmax] normalized to 0-1000, tight around it, its caption left out. Leave `text` empty: text \
printed inside a figure stays in the image.
- `reference`: the part of the page holding a list of bibliographic references, as one block with its `box_2d` \
like a figure's. Leave `text` empty: references are not translated.
- `furniture`: a running header or footer, or a page number. Leave `text` empty.
Translate every term in `terms` the way it says. Translate an acronym, with the original in parentheses the first \
time it appears. Return in `terms` only the technical terms and acronyms these pages add, each with the Spanish you \
chose. Return only the Spanish, never the original text."""


class _Term(BaseModel):
    original: str
    spanish: str


class _TextAnswer(BaseModel):
    spanish: list[str]
    terms: list[_Term]


class TranslationError(RuntimeError):
    """Gemini answered, but not with a usable translation."""


class GeminiTranslator:
    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key)

    def translate_texts(self, texts: Sequence[str], terms: Mapping[str, str]) -> TextTranslation:
        request = json.dumps({"blocks": list(texts), "terms": dict(terms)}, ensure_ascii=False)
        response = self._client.models.generate_content(
            model=MODEL,
            contents=request,
            config=types.GenerateContentConfig(
                system_instruction=_TEXT_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=_TextAnswer,
                max_output_tokens=65536,
            ),
        )
        answer = response.parsed
        if not isinstance(answer, _TextAnswer) or len(answer.spanish) != len(texts):
            raise TranslationError(f"expected {len(texts)} blocks, got: {response.text!r:.500}")
        return TextTranslation(
            texts=answer.spanish, terms={**terms, **{term.original: term.spanish for term in answer.terms}}
        )

    def read_page_batch(self, pages: Sequence[tuple[int, bytes]], terms: Mapping[str, str]) -> str:
        contents = []
        for number, image in pages:
            contents += [
                types.Part.from_text(text=f"Page {number}"),
                types.Part.from_bytes(data=image, mime_type="image/png"),
            ]
        contents.append(types.Part.from_text(text=json.dumps({"terms": dict(terms)}, ensure_ascii=False)))
        response = self._client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=_BATCH_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=PageBatchAnswer,
                # A dense four-page batch needs more than the default.
                max_output_tokens=65536,
            ),
        )
        return response.text or ""


def validate_key(api_key: str) -> bool:
    """Whether Gemini accepts the key, by reading the model's description: a call that spends no quota.

    Raises when Gemini cannot be reached or fails in another way, so that is not taken for a wrong key.
    """
    try:
        genai.Client(api_key=api_key).models.get(model=MODEL)
    except errors.ClientError as error:
        if error.code in (400, 401, 403):
            return False
        raise
    return True
