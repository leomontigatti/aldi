"""The Estimate: how long a Reading Version of some pages takes, in minutes or hours, never in requests."""

import math
from collections.abc import Sequence

# Seconds a page, from the free tier's measurements (docs/research/gemini-spike-2026-10.md): flash took 12 to 29 s
# a page of text, and its 5 requests a minute keep it near 12 s at best; flash-lite read a scanned page in 5 to 16 s,
# and a Page Batch of four needs a quarter of the requests.
_BORN_DIGITAL = 20
_SCANNED = 15


def estimate(scanned: Sequence[bool]) -> str:
    """The line she reads before she starts, for pages that are scanned or not, one flag a page."""
    seconds = sum(_SCANNED if page else _BORN_DIGITAL for page in scanned)
    minutes = max(1, math.ceil(seconds / 60))
    if minutes == 1:
        return "Tiempo estimado: un minuto"
    if minutes < 10:
        return f"Tiempo estimado: unos {minutes} minutos"
    if minutes < 55:
        return f"Tiempo estimado: unos {round(minutes / 5) * 5} minutos"
    halves = round(minutes / 30)
    hours, half = divmod(halves, 2)
    if hours == 1:
        return "Tiempo estimado: una hora y media" if half else "Tiempo estimado: una hora"
    return f"Tiempo estimado: unas {hours} horas" + (" y media" if half else "")
