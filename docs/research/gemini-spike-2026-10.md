# Gemini free-tier spike, October 2026

What the spikes of 2026-10-26 and 2026-10-27 measured, on one key's free tier, and the **Model Chain**'s
measurements of 2026-10-27 (ticket #7, `uv run just gemini-measure`). Cited by
[ADR 1](../adr/0001-gemini-free-tier-only.md) and [ADR 3](../adr/0003-reading-version-is-the-default.md). The
documents were a **Scanned PDF** (a 403-page dentistry book, 120 dpi JPEG pages, no text layer) and two
**Born-digital PDFs** (journal articles in two columns, one with a flowchart).

## Rate limits

Read from the `429` error bodies, which name the quota (`GenerateRequestsPerMinutePerProjectPerModel-FreeTier`):

| Model | Requests a minute | Notes |
| --- | --- | --- |
| `gemini-3.5-flash-lite` (`gemini-flash-lite-latest`) | 15 | Steady |
| `gemini-3.8-flash` | 5 | Often `503` (overloaded) |
| `gemini-3.5-flash` | 5 | Served as `gemini-3.6-flash`: the `429` names that model. `503` twice in a day |

Limits are per project and per model, so moving to the next model in the **Model Chain** gets fresh quota on the
same key. The daily limit is not reported until it is hit, and was not hit.

A `429` carries a `RetryInfo` with how long to wait (`"retryDelay": "1s"` when the minute was almost over). The
**Translator** skips a model for that long and goes back to it after, so a minute's limit on flash does not move
the rest of a book to flash-lite.

## Speed

A page of text from a **Born-digital PDF** (about 450 words in six blocks), through the **Translator**:

| Model | Seconds a page |
| --- | --- |
| `gemini-3.5-flash` | 11.6 and 28.8 (two runs) |
| `gemini-3.5-flash-lite` | 6.3 |
| `gemma-4-26b-a4b-it` | 84 to 144 |

At flash's 5 requests a minute, a fast run of short pages reaches the limit and the next page goes to flash-lite
for the rest of that minute.

flash-lite took 5 to 16 s per scanned page, one page a request. Four-page **Page Batches** cut the requests to
a quarter; their time per page was not measured carefully.

## The scanned path

- flash-lite read 120 dpi pages accurately, followed two columns in order and boxed photos tightly (normalized
  0-1000 boxes, cropped from the page image).
- A four-page batch asking for the original text and the Spanish came back empty with finish reason
  `RECITATION`, Gemini's block on output that repeats a source verbatim. Asking only for the Spanish passed, and
  inline emphasis came back as Markdown.
- Reference lists were the weakest output in every variant.
- `max_output_tokens` was set to 65536 so a dense four-page batch fits; the default was not tried.

## The born-digital paths

- `pdf2zh-next` 2.9.0 (BabelDOC underneath) with flash-lite kept columns and figures, but: text ragged where the
  original was justified, paragraphs split, a letter-spaced line left in English, the flowchart's boxes garbled,
  fonts shrunk to fit the longer Spanish, bold lost.
- The same with `gemini-3.5-flash` fixed the untranslated line and some splits; the references page stayed
  broken and the fonts still shrank.
- Sending the article's pages through the scanned path as images worked, but Gemini added bold that the original
  did not have and boxed vector figures (drawn, not photos) badly. The text layer and the PDF's own images are
  the better source.

## Tooling

- `pdf2zh-next` 2.9.0 fails to import with `bitstring` 5 (`BitStream`); pin `bitstring<4.4`.
- Markdown to PDF through the `typst` package and to DOCX through `pypandoc-binary` both work from one Markdown,
  with wheels for Windows and macOS. `#set par(justify: false)` gives left-aligned text.

## Gemma, the end of the Model Chain

The key reaches two Gemma models, `gemma-4-26b-a4b-it` and `gemma-4-31b-it`. Measured with the **Translator**'s
own request (`uv run just gemini-measure variants <model>`):

- `gemma-4-31b-it` answered `500 INTERNAL` to every variant: with the system instruction or without, with a
  response schema or without.
- `gemma-4-26b-a4b-it` answered `500` or hung (one request ran over 15 minutes, another timed out at 3) when the
  request carried a response schema. With the system instruction and JSON output but no schema it translated the
  page well, in 84 to 144 s, but shaped the Term List its own way until the instruction spelled out the JSON.

So the chain ends in `gemma-4-26b-a4b-it`, asked without a schema and told the answer's shape in the instruction.
At over a minute a page it is a last resort before a **Quota Pause**, not a model to translate a book with.
