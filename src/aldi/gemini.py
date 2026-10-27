"""The real Translator: Gemini through `google-genai`, on her own free-tier key (ADR 1)."""

import json
import time
from collections.abc import Callable, Mapping, Sequence

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from aldi.translator import Offline, PageBatchAnswer, QuotaExhausted, TextTranslation

# Each model has its own free quota on the same key (ADR 1); a Job moves down when one runs out.
# gemma-4-31b-it answered 500 to every request on 2026-10-27; the 26b one works (docs/research/gemini-spike-2026-10.md).
MODEL_CHAIN = ("gemini-3.5-flash", "gemini-3.5-flash-lite", "gemma-4-26b-a4b-it")
# Seconds to wait before asking a busy (503) model again; after the last, the request goes to the next model.
BUSY_WAITS = (10, 30, 90)
# Seconds a model that answered 429 is skipped when the answer gives no `retryDelay`: a minute's quota is back by then.
QUOTA_RETRY = 60

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

# Gemma fails or hangs on a request with a response schema, so it gets the answer's shape in words instead.
_TEXT_SHAPE = """

Answer with one JSON object: {"spanish": ["<one per block>"], "terms": [{"original": "...", "spanish": "..."}]}."""


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

_BATCH_SHAPE = """

Answer with one JSON object: {"pages": [{"page": N, "blocks": [{"kind": "...", "text": "...", "rows": [["..."]], \
"box_2d": [ymin, xmin, ymax, xmax]}]}], "terms": [{"original": "...", "spanish": "..."}]}."""


class _Term(BaseModel):
    original: str
    spanish: str


class _TextAnswer(BaseModel):
    spanish: list[str]
    terms: list[_Term]


class TranslationError(RuntimeError):
    """Gemini answered, but not with a usable translation."""


class GeminiTranslator:
    def __init__(
        self,
        client: genai.Client,
        chain: Sequence[str] = MODEL_CHAIN,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._chain = tuple(chain)
        self._sleep = sleep
        self._clock = clock
        # When each model that answered 429 has quota again. Flash's free limit is 5 requests a minute, so most 429s
        # are over within the minute and the next page goes back to the first model.
        self._out_of_quota_until: dict[str, float] = {}

    def translate_texts(self, texts: Sequence[str], terms: Mapping[str, str]) -> TextTranslation:
        request = json.dumps({"blocks": list(texts), "terms": dict(terms)}, ensure_ascii=False)
        response = self._generate(request, _TEXT_INSTRUCTION, _TextAnswer, _TEXT_SHAPE)
        try:
            answer = _TextAnswer.model_validate_json(response.text or "")
        except ValueError:
            answer = None
        if answer is None or len(answer.spanish) != len(texts):
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
        return self._generate(contents, _BATCH_INSTRUCTION, PageBatchAnswer, _BATCH_SHAPE).text or ""

    def _generate(
        self, contents: str | list[types.Part], instruction: str, schema: type[BaseModel], shape: str
    ) -> types.GenerateContentResponse:
        for model in self._chain:
            gemma = model.startswith("gemma")
            config = types.GenerateContentConfig(
                system_instruction=instruction + shape if gemma else instruction,
                response_mime_type="application/json",
                response_schema=None if gemma else schema,
                max_output_tokens=32768 if gemma else 65536,
            )
            if self._clock() < self._out_of_quota_until.get(model, 0):
                continue
            for wait in (*BUSY_WAITS, None):
                try:
                    return self._client.models.generate_content(model=model, contents=contents, config=config)
                except errors.APIError as error:
                    if error.code == 503 and wait is not None:
                        self._sleep(wait)
                        continue
                    if error.code == 429:
                        self._out_of_quota_until[model] = self._clock() + _retry_delay(error)
                    elif error.code != 503:
                        raise
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                    raise Offline from error
        raise QuotaExhausted


def _retry_delay(error: errors.APIError) -> float:
    """The seconds a 429 says to wait, from its `RetryInfo` (`"retryDelay": "38s"`), or `QUOTA_RETRY`."""
    details = error.details.get("error", {}).get("details", []) if isinstance(error.details, dict) else []
    for detail in details:
        if detail.get("@type", "").endswith("RetryInfo"):
            try:
                return float(detail.get("retryDelay", "").removesuffix("s"))
            except ValueError:
                break
    return QUOTA_RETRY


def validate_key(api_key: str) -> bool:
    """Whether Gemini accepts the key, by reading the model's description: a call that spends no quota.

    Raises when Gemini cannot be reached or fails in another way, so that is not taken for a wrong key.
    """
    try:
        genai.Client(api_key=api_key).models.get(model=MODEL_CHAIN[0])
    except errors.ClientError as error:
        if error.code in (400, 401, 403):
            return False
        raise
    return True
