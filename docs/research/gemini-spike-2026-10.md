# Gemini free-tier spike, October 2026

What the spikes of 2026-10-26 and 2026-10-27 measured, on one key's free tier. Cited by
[ADR 1](../adr/0001-gemini-free-tier-only.md) and [ADR 3](../adr/0003-reading-version-is-the-default.md). The
documents were a **Scanned PDF** (a 403-page dentistry book, 120 dpi JPEG pages, no text layer) and two
**Born-digital PDFs** (journal articles in two columns, one with a flowchart).

## Rate limits

Read from the `429` error bodies, which name the quota (`GenerateRequestsPerMinutePerProjectPerModel-FreeTier`):

| Model | Requests a minute | Notes |
| --- | --- | --- |
| `gemini-3.5-flash-lite` (`gemini-flash-lite-latest`) | 15 | Steady |
| `gemini-3.8-flash` | 5 | Often `503` (overloaded) |

Limits are per project and per model, so moving to the next model in the **Model Chain** gets fresh quota on the
same key. The daily limit is not reported until it is hit; `gemini-3.5-flash`'s limits were not measured.

## Speed

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
