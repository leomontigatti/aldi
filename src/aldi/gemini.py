"""The real Translator: Gemini through `google-genai`, on her own free-tier key (ADR 1)."""

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from aldi.translator import (
    KeyRefused,
    Offline,
    PageBatchAnswer,
    PageBatchReply,
    QuotaExhausted,
    Term,
    TextTranslation,
)

# One line per request's outcome, for a maintainer reading the terminal: never the text, the key or a file name.
_log = logging.getLogger(__name__)

# Each model has its own free quota on the same key (ADR 1); a Job moves down when one runs out.
# gemma-4-31b-it answered 500 to every request on 2026-10-27; the 26b one works (docs/research/gemini-spike-2026-10.md).
MODEL_CHAIN = ("gemini-3.5-flash", "gemini-3.5-flash-lite", "gemma-4-26b-a4b-it")
# Seconds to wait before asking a busy model again; after the last, the request goes to the next model.
BUSY_WAITS = (10, 30, 90)
# Gemini's answers that mean busy rather than wrong: overloaded, an internal error, its own deadline.
_BUSY_CODES = (500, 503, 504)
# A request that takes longer is cut and waited out like a busy model: a Gemma request once hung 15 minutes.
REQUEST_TIMEOUT = 5 * 60
# A connection that takes longer to open is no connection at all: Offline, not a busy model.
CONNECT_TIMEOUT = 30
# Seconds a model that answered 429 is skipped when the answer gives no `retryDelay`: a minute's quota is back by then.
QUOTA_RETRY = 60
# Mantener el diseño keeps to flash (ADR 3): the free tier's 5 requests a minute run out within each minute, so a
# 429's retry delay, or a busy model's wait, is waited out this many times in a row before it is a full Quota Pause.
LAYOUT_QUOTA_WAITS = 3

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


class _TextAnswer(BaseModel):
    spanish: list[str]
    terms: list[Term]


class TranslationError(RuntimeError):
    """Gemini answered, but not with a usable translation."""


def _connect_timeout(request: httpx.Request) -> None:
    """Gives opening the connection its own `CONNECT_TIMEOUT`: google-genai sets one timeout on every phase of a
    request. A request hook rather than a transport of our own, which would make httpx skip the system proxy."""
    request.extensions["timeout"] = {**request.extensions.get("timeout", {}), "connect": CONNECT_TIMEOUT}


def make_client(api_key: str) -> genai.Client:
    """Gemini's client for her key, its requests cut after `REQUEST_TIMEOUT`, or `CONNECT_TIMEOUT` while
    connecting."""
    hooks = {"request": [_connect_timeout]}
    options = types.HttpOptions(timeout=REQUEST_TIMEOUT * 1000, client_args={"event_hooks": hooks})
    return genai.Client(api_key=api_key, http_options=options)


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
        # Mantener el diseño's waits in a row since flash last answered.
        self._layout_waits = 0

    def translate_texts(self, texts: Sequence[str], terms: Mapping[str, str]) -> TextTranslation:
        request = json.dumps({"blocks": list(texts), "terms": dict(terms)}, ensure_ascii=False)
        model, response = self._generate(request, _TEXT_INSTRUCTION, _TextAnswer, _TEXT_SHAPE)
        try:
            answer = _TextAnswer.model_validate_json(response.text or "")
        except ValueError:
            answer = None
        if answer is None or len(answer.spanish) != len(texts):
            error = TranslationError(f"expected {len(texts)} blocks, got: {response.text!r:.500}")
            raise _from_model(error, model)
        # A blank answer for a text with words would drop it from her page without a trace.
        if any(text.strip() and not spanish.strip() for text, spanish in zip(texts, answer.spanish, strict=True)):
            error = TranslationError(f"a blank translation of a non-empty block: {response.text!r:.500}")
            raise _from_model(error, model)
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
        model, response = self._generate(contents, _BATCH_INSTRUCTION, PageBatchAnswer, _BATCH_SHAPE)
        return PageBatchReply(response.text or "", model)

    def answer_layout_prompt(self, prompt: str) -> str:
        """Asks flash once. Out of quota or busy, raises `QuotaExhausted` with the wait before asking again, for the
        Job to wait out as a Quota Pause she sees; after `LAYOUT_QUOTA_WAITS` such waits in a row, without one."""
        model = self._chain[0]
        try:
            answer = self._generate(prompt, chain=(model,), busy_waits=())[1].text or ""
        except QuotaExhausted:
            waited, self._layout_waits = self._layout_waits, self._layout_waits + 1
            if waited >= LAYOUT_QUOTA_WAITS:
                self._layout_waits = 0
                raise
            quota = self._out_of_quota_until.get(model, 0) - self._clock()
            raise QuotaExhausted(quota if quota > 0 else BUSY_WAITS[waited]) from None
        self._layout_waits = 0
        return answer

    def _generate(
        self,
        contents: str | list[types.Part],
        instruction: str | None = None,
        schema: type[BaseModel] | None = None,
        shape: str = "",
        chain: Sequence[str] | None = None,
        busy_waits: Sequence[float] = BUSY_WAITS,
    ) -> tuple[str, types.GenerateContentResponse]:
        """Gemini's answer from the first model of `chain`, the Model Chain by default, that takes the request, and
        that model; a JSON answer matching `schema` when there is one.

        A model that refuses her key access (a `403`) is unavailable to her, like one out of quota; only when every
        model refuses it, none skipped for its quota, is the key refused."""
        skipped = refused = 0
        for model in chain or self._chain:
            gemma = model.startswith("gemma")
            config = types.GenerateContentConfig(
                system_instruction=(instruction + shape if gemma else instruction) if instruction else None,
                response_mime_type="application/json" if schema else None,
                response_schema=None if gemma else schema,
                max_output_tokens=32768 if gemma else 65536,
            )
            if self._clock() < self._out_of_quota_until.get(model, 0):
                skipped += 1
                continue
            try:
                response = self._ask(model, contents, config, busy_waits)
            except _Forbidden:
                refused += 1
                continue
            if response is not None:
                return model, response
        if refused and not skipped and refused == len(chain or self._chain):
            raise KeyRefused
        raise QuotaExhausted

    def _ask(
        self,
        model: str,
        contents: str | list[types.Part],
        config: types.GenerateContentConfig,
        busy_waits: Sequence[float],
    ) -> types.GenerateContentResponse | None:
        """The model's answer, asked again after each of `busy_waits` while it is busy; None when it stays busy or
        is out of quota."""
        for wait in (*busy_waits, None):
            try:
                response = self._client.models.generate_content(model=model, contents=contents, config=config)
                _log.info("%s: answered", model)
                return response
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as error:
                # A proxy that will not open the way (one asking for a password) leaves her offline as much.
                _log.info("%s: offline", model)
                raise Offline from error
            except httpx.TimeoutException:
                # Cut off or kept waiting: Gemini is busy, not out of reach.
                outcome = "timed out"
            except (httpx.ReadError, httpx.RemoteProtocolError):
                outcome = "connection dropped"
            except errors.APIError as error:
                if error.code == 429:
                    delay = _retry_delay(error)
                    _log.info("%s: 429, its quota back in %g s", model, delay)
                    self._out_of_quota_until[model] = self._clock() + delay
                    return None
                _raise_unless_busy(error, model)
                outcome = str(error.code)
            if wait is None:
                _log.info("%s: %s, giving up on it", model, outcome)
            else:
                _log.info("%s: %s, waiting %g s", model, outcome, wait)
                self._sleep(wait)
        return None


def _raise_unless_busy(error: errors.APIError, model: str) -> None:
    """Raises what Gemini's error means unless it means the model is busy: the model refusing her key access, the
    key refused, or another error carrying the model for its Error Report."""
    if error.code == 403:
        _log.info("%s: 403, access refused", model)
        raise _Forbidden from error
    if _key_refused(error):
        _log.info("%s: key refused", model)
        raise KeyRefused from error
    if error.code not in _BUSY_CODES:
        _log.info("%s: %s, failed", model, error.code)
        error.gemini_model = model
        raise error


class _Forbidden(Exception):
    """A model refused her key access: that model is unavailable to her, the key may still be good."""


def _from_model(error: Exception, model: str) -> Exception:
    """`error`, carrying the model whose answer it is about for its Error Report."""
    error.gemini_model = model
    return error


def _key_refused(error: errors.APIError) -> bool:
    invalid = error.code == 400 and "API_KEY_INVALID" in json.dumps(error.details, default=str)
    return invalid or error.code == 401


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
    # Held until the call returns: a client nobody holds closes its connection before the request is sent.
    with make_client(api_key) as client:
        try:
            client.models.get(model=MODEL_CHAIN[0])
        except errors.ClientError as error:
            if error.code in (400, 401, 403):
                return False
            raise
    return True
