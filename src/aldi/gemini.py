"""The real Translator: Gemini through `google-genai`, on her own free-tier key (ADR 1)."""

import json
import time
from collections.abc import Callable, Mapping, Sequence

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from aldi.translator import Offline, QuotaExhausted, TextTranslation

# Each model has its own free quota on the same key (ADR 1); a Job moves down when one runs out.
MODEL_CHAIN = ("gemini-3.5-flash", "gemini-3.5-flash-lite", "gemma-4-31b-it")
# Seconds to wait before asking a busy (503) model again; after the last, the request goes to the next model.
BUSY_WAITS = (10, 30, 90)
# Seconds a model that answered 429 is skipped when the answer gives no `retryDelay`: a minute's quota is back by then.
QUOTA_RETRY = 60

_TEXT_INSTRUCTION = """\
You translate a dentistry textbook or journal article from English or Portuguese into Spanish for a dentistry \
student. Use standard Spanish dental terminology.

You receive a JSON object with `blocks`, the text blocks of one page in reading order, and `terms`, the technical \
terms and acronyms already translated in this document with the Spanish chosen for each. Return `spanish`: \
exactly one Spanish translation per block, in the same order, as plain text. Translate every term in `terms` the \
way it says. Translate an acronym, with the original in parentheses the first time it appears. Return in `terms` \
only the technical terms and acronyms this page adds, each with the Spanish you chose. Return only the Spanish, \
never the original text."""


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
        response = self._generate(
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

    def _generate(self, **request) -> types.GenerateContentResponse:
        for model in self._chain:
            if self._clock() < self._out_of_quota_until.get(model, 0):
                continue
            for wait in (*BUSY_WAITS, None):
                try:
                    return self._client.models.generate_content(model=model, **request)
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
