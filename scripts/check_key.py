"""Asks Gemini about a key the way `aldi.gemini.validate_key` does, and prints what it answers: the status and body
of its error, or that it took the key. Reading a model's description spends no quota.

    check_key.py KEY

For a made-up key, to see how Gemini refuses one. Never pass her key here: the output is pasted into notes.
"""

import json
import sys

from google import genai
from google.genai import errors

from aldi.gemini import MODEL_CHAIN


def main(key: str) -> None:
    # Kept in a name: a client nobody holds closes its connection before the request is sent.
    client = genai.Client(api_key=key)
    try:
        client.models.get(model=MODEL_CHAIN[0])
    except errors.APIError as error:
        print(f"{type(error).__name__} {error.code} {error.status}")
        print(json.dumps(error.details, indent=2, default=str))
        return
    print("accepted")


if __name__ == "__main__":
    main(*sys.argv[1:])
