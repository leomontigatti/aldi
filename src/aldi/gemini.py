"""The real Translator: Gemini through `google-genai`, on her own free-tier key (ADR 1)."""

import json
from collections.abc import Mapping, Sequence

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from aldi.translator import TextTranslation

MODEL = "gemini-3.5-flash"

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
