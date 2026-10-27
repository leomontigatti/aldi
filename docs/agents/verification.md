# Verification

How to prove a change she would see: the app window, a **Job**'s progress, the PDF and DOCX it writes. The app
does not exist yet; the ticket that adds its window adds the `dev` recipe and fills § Launch and § Feature map.

## Launch

`uv run just dev` (added with the app window) starts the app from this worktree on a free `localhost` port,
without pywebview's native window, and prints the URL. It reads the key from `.env`. The output of every **Job**
goes under this worktree's git-ignored `jobs/`.

## Doctor

- Read the app's log in the terminal `dev` runs in; the **Job** folder under `jobs/` holds each page's Gemini
  response, so a bad page is diagnosed from what Gemini returned, not from guesses.
- A `429` is quota, not a bug: the **Model Chain** moves on, then a **Quota Pause**. A `503` is Gemini
  overloaded; it retries.

## Drive

Drive a **Job** the way she would: drop a PDF, set the **Page Range** and **Output Mode**, wait, open the result.

- **In a T3 thread**: the preview tools on the URL `dev` printed, as the shared `implement` skill's
  `PREVIEW.md` says. Drop a file with `preview_act` (`action: "upload"`).
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
| (none yet) | |
