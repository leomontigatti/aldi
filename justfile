# Every command an agent or a person runs in this repo. `just` lists them; see
# docs/agents/validation.md for when to use which. Run through uv: `uv run just <recipe>`.

set shell := ["bash", "-euo", "pipefail", "-c"]
# Git for Windows' bash: a bare `bash` on Windows can resolve to the WSL launcher in System32.
set windows-shell := ["C:/Program Files/Git/bin/bash.exe", "-euo", "pipefail", "-c"]

# List recipes
default:
    @just --list --unsorted

# Install pinned tools and the pre-commit git hook; create .env and samples/ if missing
setup:
    uv sync --locked
    uv run pre-commit install --install-hooks
    [ -e .env ] || [ -L .env ] || cp .env.example .env
    [ -e samples ] || [ -L samples ] || mkdir samples
    {{ just_executable() }} layout-assets

# Download what Mantener el diseño's BabelDOC needs (~345 MB) into ~/.cache/babeldoc, as `aldi setup` does; the
# tests need it. Keeps the files already there and intact.
layout-assets:
    uv run --locked python -c "from aldi.layout import fetch_assets; fetch_assets()"

# Re-resolve uv.lock after a dependency change in pyproject.toml, then sync
lock:
    uv lock
    uv sync --locked
    just constraints

# Export uv.lock's pins for the install line, which installs Aldi as a uv tool under them
constraints:
    uv export --quiet --locked --no-default-groups --no-hashes --no-emit-project --no-annotate --format requirements.txt -o install-constraints.txt

# The app on a free localhost port, without the native window; jobs, results and settings under this worktree's jobs/
dev:
    uv run --locked python -m aldi --no-window --data-dir jobs --documents-dir jobs/Documentos --config-dir jobs/config

# A two-page Born-digital PDF built like the tests' ones, to drive `dev` without her documents
test-pdf path="jobs/prueba.pdf":
    uv run --locked python tests/build_pdf.py {{ quote(path) }}

# Measure the Model Chain on the key in .env (spends real quota): `models`, `page`, `variants`, `burst` (each takes a model)
[positional-arguments]
gemini-measure *args:
    uv run --locked python scripts/measure_gemini.py "$@"

# How Gemini answers for a key, as the Ajustes check asks (spends no quota): a made-up KEY, never hers
check-key key:
    uv run --locked python scripts/check_key.py {{ quote(key) }}

# Peak memory of a test run (Linux): the named files or node ids; stops past ALDI_TEST_MEMORY_LIMIT_MB (4096)
[positional-arguments]
test-memory *args:
    uv run --locked python scripts/test_memory.py "$@"

# PDF pages side by side in one PNG, as evidence of a real-document check: OUT.png PDF:PAGES [PDF:PAGES ...]
[positional-arguments]
side-by-side *args:
    uv run --locked python scripts/side_by_side.py "$@"

# What the source reader makes of a PDF's pages (kinds, boxes, text, font sizes): PDF PAGES
[positional-arguments]
blocks *args:
    uv run --locked python scripts/blocks.py "$@"

# All linters and formatters on every file (fixes what it can; rerun to confirm)
lint:
    uv run pre-commit run --all-files --show-diff-on-failure

# The tests, or the named files and node ids. Never call Gemini.
[positional-arguments]
test *args:
    uv run --locked pytest "$@"

# Everything that must pass before a commit is done: lint, then tests. Failures only.
gate:
    #!/usr/bin/env bash
    set -uo pipefail
    fail=0
    if ! out=$(uv run pre-commit run --all-files 2>&1); then
      grep -vE '\.\.\.+(Passed|\(no files to check\)Skipped)$' <<<"$out"; fail=1
    fi
    [[ $fail == 0 ]] && echo "lint: PASS" || echo "lint: FAIL (files may have been auto-fixed; rerun)"
    uv run --locked pytest -q || fail=1
    [[ $fail == 0 ]] && echo "gate: PASS" || { echo "gate: FAIL"; exit 1; }

# install.sh on this checkout instead of a Release, into a throwaway folder, as CI's install job does. Linux only:
# elsewhere `aldi setup` would overwrite this machine's own reporting token and launcher.
install-check:
    #!/usr/bin/env bash
    set -euo pipefail
    [[ $(uname -s) == Linux ]] || { echo "install-check runs on Linux only"; exit 1; }
    home=$(mktemp -d)
    trap 'rm -rf "$home"' EXIT
    export ALDI_SOURCE=$PWD ALDI_REPORT_TOKEN=not-a-token UV_TOOL_DIR=$home/tools UV_TOOL_BIN_DIR=$home/bin
    export XDG_CONFIG_HOME=$home/config XDG_DATA_HOME=$home/data
    sh install.sh
    "$home/bin/aldi" --help >/dev/null
    grep -q not-a-token "$home/config/Aldi/settings.json"
    # How the app tells an install from a checkout, and so whether it offers updates (aldi.update).
    test -f "$home/tools/aldi/uv-receipt.toml"
    echo "install-check: PASS"

# Check a PR title the way CI does (Conventional Commit; it becomes the squash commit subject)
pr-title title:
    PR_TITLE={{ quote(title) }} python3 .github/scripts/check_pr_title.py

# Create or update the GitHub labels in .github/labels.json (idempotent; needs gh and jq)
labels-sync:
    jq -r '.groups[][] | [.name, .color, .description] | @tsv' .github/labels.json | while IFS=$'\t' read -r name color description; do gh label create "$name" --color "$color" --description "$description" --force; done
