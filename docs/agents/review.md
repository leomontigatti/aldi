# Review

What the shared review (`implement` step 5, `code-review`, `pr`) needs to know about this repo: where a diff is
risky, which findings are the user's call, and where the code is likely to be hurt.

## Full review

A diff that touches any of these gets the full review (Standards, Spec and correctness):

- **Her laptop**: the installer, the launcher and the update path. A broken one leaves her without the app and
  nobody beside her to fix it.
- **The key and her documents**: where the Gemini key is read, stored and sent, and what keeps the key and
  `samples/` out of git (`.gitignore`, the links `t3.json` makes).
- **Dependency pins**: `pyproject.toml` and `uv.lock`, since a bump reaches her laptop with the next release.
- **Error Reports and their token**: what an **Error Report** collects, redacts and sends, and where the GitHub
  token is read and stored. Every report is public.
- **Her files**: anything that writes, moves or deletes files outside the app's own **Job** folders.

The `review-paths` block is the same list as paths, for `review.py tier`. The areas are named in paths the app
does not have yet; when a module lands under another name, update its line in the same PR.

```review-paths
full laptop: ^(install\.(ps1|sh)|src/aldi/(install|update|reinstall|launcher)|scripts/install)
full key: ^(\.gitignore|t3\.json|src/aldi/(settings|config|keys?|gemini)\b)
full report: ^src/aldi/(reports?|reporting|errors?)\b
full files: ^src/aldi/results\b
full pins: ^(pyproject\.toml|uv\.lock|install-constraints\.txt)$
quiet: ^\.pre-commit-config\.yaml$
quiet: ^\.github/dependabot\.yml$
quiet: ^\.env\.example$
```

## Always ask

A finding in one of these that the issue does not settle is the user's call:

- Anything that sends her documents, or text from them, anywhere but Gemini.
- What an **Error Report** sends to the public tracker, and when the app files one.
- The install, launch or update flow, and what a release changes on her laptop.
- Spending quota differently: the **Page Batch** size, the **Model Chain**, retries.
- A term defined in `GLOSSARY.md`.

## Hotspots

Where this code is likely to be hurt, for the correctness brief:

- Resume after a **Quota Pause** or a closed app: a page translated twice, skipped, or out of order; a **Page
  Batch** whose answer covers fewer pages than were sent, or merges two.
- Gemini's answer taken on trust: malformed JSON, a **Figure** box outside the page or inverted, an empty
  `spanish` field, text left in Portuguese.
- Windows: backslashes and drive letters, non-ASCII file names (`Visão`, `Corrêa`), a file still open while it is
  renamed, paths longer than 260 characters, and text written without `encoding="utf-8"`.
- A Spanish accent or `ñ` lost in a font, in the PDF or the DOCX.
- The quota itself: a loop that retries without backing off burns the day's limit in a minute.

## Breadth

- **Both systems**: a change that touches files, paths, processes or the launcher names what it does on Windows
  and on macOS.
- **Both kinds of document**: a change to reading or output names what it does to a **Born-digital PDF** and to a
  **Scanned PDF**.
- **Docs**: by the rules in [workflows.md](workflows.md#documentation).

## One-way doors

- A release tag: her laptop installs it on the next update, and a broken one may also break the update button.
  The tag is `v` plus `pyproject.toml`'s version (the app compares the two), or the release workflow makes no
  Release of it.
- Making the key or her documents leave her laptop for anywhere new.
- An **Error Report** that puts document text, a file name or a key in a public issue: it cannot be unpublished.
- Deleting anything outside the app's own **Job** folders.
