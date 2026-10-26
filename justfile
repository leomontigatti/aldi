# Every command an agent or a person runs in this repo. `just` lists them; see
# docs/agents/validation.md for when to use which. Run through uv: `uv run just <recipe>`.

set shell := ["bash", "-euo", "pipefail", "-c"]

# List recipes
default:
    @just --list --unsorted

# Install pinned tools and the pre-commit git hook; create .env and samples/ if missing
setup:
    uv sync --locked
    uv run pre-commit install --install-hooks
    [ -f .env ] || cp .env.example .env
    mkdir -p samples

# All linters and formatters on every file (fixes what it can; rerun to confirm)
lint:
    uv run pre-commit run --all-files --show-diff-on-failure

# The tests, or the named files and node ids. Never call Gemini.
test *args:
    uv run --locked pytest {{ args }}

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

# Check a PR title the way CI does (Conventional Commit; it becomes the squash commit subject)
pr-title title:
    PR_TITLE={{ quote(title) }} python3 .github/scripts/check_pr_title.py

# Create or update the GitHub labels in .github/labels.json (idempotent; needs gh and jq)
labels-sync:
    jq -r '.groups[][] | [.name, .color, .description] | @tsv' .github/labels.json | while IFS=$'\t' read -r name color description; do gh label create "$name" --color "$color" --description "$description" --force; done
