# 3. The Reading Version is the default, for every kind of document

Date: 2026-10-27. Status: accepted.

## Context

The first plan gave a **Born-digital PDF** the **Layout-kept Translation** and a **Scanned PDF** the **Reading
Version**. On the spike's two journal articles, `pdf2zh-next` (its layout engine is BabelDOC) kept the columns
and figures but shrank fonts to fit the longer Spanish, dropped bold, split paragraphs in two, left a
letter-spaced line in English and garbled a flowchart. A stronger model fixed some pages, not those faults. She
reads, highlights and edits the result, so legible, editable text matters more than a faithful page.

Treating a born-digital article as page images and sending it through the scanned path worked, but Gemini
invented bold that was not there and boxed the vector figures badly.

## Decision

- The **Reading Version** is the pre-selected **Output Mode** for every **Source Document**: one column,
  left-aligned with no justification (the user dislikes justified text), a **Page Marker** where each original
  page starts, written as a PDF and a DOCX.
- A **Born-digital PDF**'s **Reading Version** is built from its own text layer and its own figures; Gemini only
  translates the text. A **Scanned PDF**'s pages are read by Gemini in **Page Batches**.
- A document that mixes both kinds of page is routed page by page.
- **Mantener el diseño** (the **Layout-kept Translation**) stays as the second choice, offered only when every
  page in the **Page Range** has a text layer. It runs `pdf2zh-next` with `gemini-3.5-flash` and without its
  automatic glossary extraction.

## Consequences

- Two ways of reading a born-digital page (its text layer, or Gemini on its image) collapse into one; the scanned
  path is only for pages with no text.
- The **Reading Version** does not look like the original. The **Page Markers** are what tie it back.
- Text inside a **Figure** stays as printed, in English or Portuguese.
