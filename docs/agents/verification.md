# Verification

How to prove a change she would see: the app window, a **Job**'s progress, the PDF and DOCX it writes.

## Launch

`uv run just dev` starts the app from this worktree on a free `127.0.0.1` port, without the native window, and
prints the URL (`Aldi: http://127.0.0.1:<port>/`). It reads the key from `.env` and stops at once without one.
Everything a **Job** writes goes under this worktree's git-ignored `jobs/`: each **Job**'s folder in `jobs/jobs/`,
and the results that would land in her Documents folder in `jobs/Documentos/Aldi/<nombre>/`.

## Doctor

- Read the app's log in the terminal `dev` runs in. A **Job**'s folder under `jobs/jobs/<id>/` holds
  `job.json`, each page's text and Gemini answer in `pages/`, the Markdown and Typst the results were made from,
  and `error.txt` when the **Job** failed, so a bad page is diagnosed from what Gemini returned, not from guesses.
- A `429` is quota, not a bug: the **Model Chain** moves on, then a **Quota Pause**. A `503` is Gemini
  overloaded; it retries.

## Drive

Drive a **Job** the way she would: drop a PDF, set the **Page Range** and **Output Mode**, wait, open the result.

- **In a T3 thread**: the preview tools on the URL `dev` printed, as the shared `implement` skill's
  `PREVIEW.md` says. Drop a file with `preview_upload`.
- **Outside T3**: the same in a browser, or the job function from a test.

Use a PDF the tests build when it proves the change, since it spends no quota. A real document from `samples/`
(a few pages through the **Page Range**) when the change is about reading or translating quality.

## Evidence

Evidence is for the user, in the thread; the PR carries none of it.

- The app window: a `preview_snapshot` with `save: true`, embedded by its absolute path.
- A result: a page of the output PDF rendered to PNG beside the same original page, embedded by absolute path.
  Never commit either, since they are made from her documents.
- A test that failed without the change is evidence too; a list of green commands is not.

## Cleanup

Close the preview tab with `t3_preview_close`, then stop `dev` with Ctrl-C in its terminal, or `TaskStop` on the
background task that runs it. Never `pkill`. `jobs/` stays; delete a worktree's `jobs/` only when the user asks.

## Feature map

Which flow proves which area. Filled in as each area lands.

| Area | Flow |
| ---- | ---- |
| Reading Version of a **Born-digital PDF** | Drop the PDF (or `Elegir archivo`); the list shows `Traduciendo… Página N de M`, then `Lista` with the results' folder. Open the PDF and DOCX there: Spanish text, one column, left-aligned, a `p. N del original` at each original page; the original's images near their text with the Spanish caption under them, its tables as tables, its bold and italics, its references untranslated, no running headers or page numbers (`uv run just test-pdf` builds such an article). |
| A file that is not a PDF | Drop it; `Este archivo no es un PDF.` appears under the drop zone and no **Job** starts. |
