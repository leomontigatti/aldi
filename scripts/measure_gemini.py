"""Measures the Model Chain on the key in `.env`: which models the key reaches, a page's time, the 429 bodies.

Spends real free quota: run it only to refresh `docs/research/gemini-spike-2026-10.md`.
"""

import argparse
import json
import time
from pathlib import Path

from google import genai
from google.genai import types

from aldi.settings import Settings, default_config_dir

# About the length of a page of her books: ~450 words in six blocks.
PAGE = [
    "Chapter 7. Pulp and Periapical Disease",
    "The dental pulp is a loose connective tissue enclosed within the rigid walls of dentin. Because it has no "
    "room to swell, inflammation raises the intrapulpal pressure and compresses the thin-walled venules, which "
    "can lead to local necrosis that spreads coronally to apically through the canal system.",
    "Reversible pulpitis is characterized by a sharp, short pain provoked by cold or sweet stimuli that subsides "
    "within seconds after the stimulus is removed. The tooth is not tender to percussion, and the periapical "
    "radiograph shows no widening of the periodontal ligament (PDL) space. Removing the cause, such as a carious "
    "lesion or a leaking restoration, and placing a sedative dressing usually resolves the symptoms.",
    "Irreversible pulpitis, by contrast, produces spontaneous, lingering pain that may radiate to the ear or the "
    "temple and is often worse when the patient lies down. Heat may exacerbate the pain while cold relieves it in "
    "advanced stages. The treatment of choice is pulpectomy followed by root canal treatment (RCT), or extraction "
    "when the tooth cannot be restored.",
    "Pulp necrosis is frequently asymptomatic. The tooth does not respond to electric pulp testing (EPT) or to "
    "cold, and it may show a gray discoloration of the crown. When bacteria from the necrotic canal reach the "
    "periapical tissues, an apical periodontitis develops, which can be symptomatic, with pain on biting and "
    "percussion, or asymptomatic, with a radiolucency at the apex detected on a routine radiograph.",
    "Table 7.1 summarizes the clinical and radiographic findings of each diagnosis. The clinician should always "
    "combine thermal tests, EPT, percussion, palpation and periodontal probing before deciding on the treatment, "
    "since no single test is reliable on its own.",
]


def models(client: genai.Client) -> None:
    for model in client.models.list():
        if "generateContent" in (model.supported_actions or []):
            print(model.name, model.input_token_limit, model.output_token_limit)


def page(client: genai.Client, model: str, runs: int) -> None:
    from aldi.gemini import GeminiTranslator

    translator = GeminiTranslator(client, chain=(model,))
    for _ in range(runs):
        started = time.monotonic()
        try:
            answer = translator.translate_texts(PAGE, {})
        except Exception as error:
            print(f"{model}: {type(error).__name__} after {time.monotonic() - started:.1f} s: {error}")
            continue
        print(f"{model}: {time.monotonic() - started:.1f} s, {len(answer.texts)} blocks, {len(answer.terms)} terms")
        print(json.dumps(answer.texts[:2], ensure_ascii=False)[:400])


def variants(client: genai.Client, model: str) -> None:
    """Which parts of the translator's request config the model accepts, one request per variant."""
    from aldi.gemini import _TEXT_INSTRUCTION, _TextAnswer

    request = json.dumps({"blocks": PAGE, "terms": {}}, ensure_ascii=False)
    for name, instruction_in_config, schema in (
        ("instruction in config, no schema", True, False),
        ("instruction in prompt, schema", False, True),
        ("instruction in prompt, no schema", False, False),
    ):
        config = types.GenerateContentConfig(response_mime_type="application/json", max_output_tokens=32768)
        if instruction_in_config:
            config.system_instruction = _TEXT_INSTRUCTION
        if schema:
            config.response_schema = _TextAnswer
        contents = request if instruction_in_config else f"{_TEXT_INSTRUCTION}\n\n{request}"
        started = time.monotonic()
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
            answer = _TextAnswer.model_validate_json(response.text)
            print(
                f"{name}: {time.monotonic() - started:.1f} s, {len(answer.spanish)} blocks, {len(answer.terms)} terms"
            )
        except Exception as error:
            print(f"{name}: {type(error).__name__} after {time.monotonic() - started:.1f} s: {str(error)[:300]}")


def burst(client: genai.Client, model: str, requests: int) -> None:
    started = time.monotonic()
    for number in range(1, requests + 1):
        try:
            client.models.generate_content(model=model, contents="Di «hola».")
            print(f"{number} ok at {time.monotonic() - started:.1f} s")
        except genai.errors.APIError as error:
            print(f"{number} {error.code} at {time.monotonic() - started:.1f} s: {json.dumps(error.details)}")
            if error.code == 429:
                return


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("models", help="the models the key reaches")
    timing = commands.add_parser("page", help="a page's translation time on one model")
    timing.add_argument("model")
    timing.add_argument("--runs", type=int, default=1)
    probe = commands.add_parser("variants", help="which parts of the request config a model accepts")
    probe.add_argument("model")
    limit = commands.add_parser("burst", help="tiny requests back to back until the first 429, to read its quota")
    limit.add_argument("model")
    limit.add_argument("--requests", type=int, default=12)
    args = parser.parse_args()
    key = Settings(default_config_dir(), dev_env_file=Path(".env")).key
    # A request that hangs (Gemma did, for over 15 minutes) ends after 3 minutes instead.
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=180_000))
    if args.command == "models":
        models(client)
    elif args.command == "page":
        page(client, args.model, args.runs)
    elif args.command == "variants":
        variants(client, args.model)
    else:
        burst(client, args.model, args.requests)
