# 1. Gemini's free tier is the only engine

Date: 2026-10-26. Status: accepted.

## Context

The user wants no new expense. The documents are mostly **Scanned PDFs** (a 403-page, 120-dpi dentistry book
with no text layer, 52 MB) and some **Born-digital PDFs** (journal articles). Candidates: DeepL API Free (500k
characters a month, a 50k minimum per PDF, text only), Google Cloud document translation ($0.08 a page),
Claude or Codex through a subscription's CLI, local models (NLLB is non-commercial; the rest translate dental
Spanish poorly), and Gemini's free API.

A spike on 2026-10-26 (9 pages of the book, both articles) showed `gemini-3.5-flash-lite` reading the scan pages
accurately, boxing the figures tightly and translating in usable dental Spanish at 5 to 16 s a page;
`pdf2zh-next` with the same model kept both articles' layout. Measured free limits: 15 requests a minute for
`gemini-3.5-flash-lite`, 5 for `gemini-3.8-flash` (often overloaded); limits are per project and per model. The
daily limit is not reported until it is hit.

## Decision

Gemini's free tier does the reading and the translating, on the user's own key. A **Scanned PDF** goes in **Page
Batches** of four pages, each request returning the original text, the Spanish and the **Figure** boxes. When a
model's quota runs out the **Job** moves along the **Model Chain** (flash-lite, then flash, then Gemma), and when
all are out it takes a **Quota Pause**.

## Consequences

- One key, one engine, no billing. DeepL returns only if she finds Gemini's Spanish worse.
- The user's documents go to Google, and the free tier may use them for training: accepted, they are published
  study material.
- No extra keys or accounts to multiply the free quota: the **Model Chain** is the legitimate equivalent.
- A Claude/Codex subscription cannot serve as a fallback on her laptop.

## Amendment (2026-10-27)

A second spike, on more of the book and both articles, changed four parts of the decision. Measurements are in
[the spike notes](../research/gemini-spike-2026-10.md).

- **The Model Chain starts with `gemini-3.5-flash`**, then flash-lite, then Gemma. Flash read and translated
  noticeably better, both in the **Reading Version** and through `pdf2zh-next`; flash-lite stays as the next
  model when flash's quota runs out.
- **A Page Batch returns only the Spanish and the Figure boxes, never a transcription of the original.** Gemini
  blocked a four-page batch that asked for both with finish reason `RECITATION`, its block on output that
  repeats a source verbatim. Asking only for the translation passed. Bold and
  italics come back marked inline in the Spanish.
- **References are left untranslated.** Every attempt garbled the reference lists, and nobody cites a reference
  in Spanish.
- **A Term List goes with every Page Batch** and is saved with the **Job**, so a term translated one way on page 12 is translated the same way
  on page 300. An acronym is translated, with the original in parentheses the first time.
