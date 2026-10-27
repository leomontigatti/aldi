# Commands

`justfile` is the complete list of commands for this repo. Run them as `uv run just <recipe>`
(`uv run just` lists them). If a task needs a command that isn't a recipe, add the recipe
first instead of running an ad-hoc command, so the next run is the same.

## First time on a machine

`uv run just setup`: tools, the pre-commit hook, `.env` from `.env.example`, an empty `samples/`, and BabelDOC's
assets (`uv run just layout-assets`). Put a
Gemini key in `.env` and her sample PDFs in `samples/` only when a real-document run is needed.

## As you go

`uv run just test <path or node id>` each time a slice goes green. After a merge, `uv run just lint` and
`uv run just test`.

A change to the install line (`install.ps1`, `install.sh`, `aldi setup` in `src/aldi/install.py`): `uv run just
install-check` runs `install.sh` on this checkout into a throwaway folder, on Linux only. CI's `install` job runs
both scripts on Windows and macOS; the window itself is checked by hand there ([verification.md](verification.md)).

## Before committing

`uv run just gate`. It runs every linter, then every test, and prints only failures plus a summary line. Lint
may auto-fix files: rerun until it passes.

The tests never call Gemini or the network: the translator is a seam, and the tests use a fake behind it. They
build their own small PDFs (born-digital ones with a PDF library, scanned ones as page images) rather than reading
`samples/`.

One exception: Mantener el diseño's tests run BabelDOC for real, and BabelDOC needs ~345 MB of fonts, a layout model
and tiktoken's encoding in `~/.cache/babeldoc`. `uv run just layout-assets` downloads them once (`just setup` runs
it); the tests themselves then stay offline. CI fills the same folder in one step before the tests, kept with
`actions/cache` while uv.lock keeps the same BabelDOC version.

## CI

`.github/workflows/ci.yml` runs on every PR: `checks` (every pre-commit hook, plus gitleaks over the PR's
commits) and `test` on Ubuntu, Windows and macOS. `pr-title.yml` checks the title; check one before opening a PR
with `uv run just pr-title "<title>"`. Dependabot proposes action and uv bumps after a 7-day cooldown.

## Agent hooks

The project's Claude Code and Codex hooks ([agent-configuration.md](agent-configuration.md#project-hooks)) run the
same checks as you go: `ruff` formats each Python file an agent edits, and the Stop hook runs pre-commit on the
changed files before a turn can end. They never run the tests, and they do not replace `uv run just gate`.
