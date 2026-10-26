#!/usr/bin/env bash
#
# PostToolUse hook (matcher: Write|Edit), wired from both `.claude/settings.json` and
# `.codex/hooks.json`.
#
# Formats and fixes the Python files the tool just wrote, so formatting stops being anyone's job:
# `ruff check --fix --exit-zero`, then `ruff format`, the same two ruff hooks pre-commit runs.
#
# The two harnesses describe the edit differently. Claude Code's `Write`/`Edit` send
# `tool_input.file_path`. Codex matches `Write|Edit` against `apply_patch`, whose
# `tool_input.command` is the patch itself; its `*** Add File:`, `*** Update File:` and
# `*** Move to:` headers name the files, relative to the payload's `cwd`.
#
# Three things about this hook are deliberate:
#
# - It always exits 0 and never writes to stderr. PostToolUse stderr is fed back to the agent,
#   and a reformat is not something the agent should react to.
# - It runs the tools through `uv run --locked`, so the versions are the ones in uv.lock.
# - Only `.py` files are touched; the other formatters run in pre-commit, which the Stop hook and
#   `uv run just lint` both reach.
#
# See docs/agents/agent-configuration.md § Project hooks.

set -uo pipefail

input="$(cat)"
cwd="$(printf '%s' "$input" | jq -r '.cwd // ""' 2>/dev/null)"

# One path per line: Claude Code's `file_path`, or the file headers of a Codex patch. A deleted
# file has nothing to format, so `*** Delete File:` is skipped.
files="$(
  printf '%s' "$input" | jq -r '
    .tool_input.file_path // empty,
    (.tool_input.command // "" | strings
      | split("\n")[]
      | capture("^\\*\\*\\* (Add File|Update File|Move to): (?<path>.+)$")?
      | .path)
  ' 2>/dev/null
)"

[ -n "$files" ] || exit 0
command -v uv >/dev/null 2>&1 || exit 0

project_dir="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

while IFS= read -r file; do
  case "$file" in
    /*) ;;
    *) [ -n "$cwd" ] && file="$cwd/$file" ;;
  esac
  [ -f "$file" ] || continue
  case "$file" in
    *.py) ;;
    *) continue ;;
  esac

  # Fixes first, then format: the order pre-commit runs them in, so the result is what it accepts.
  (
    cd "$project_dir" &&
      uv run --locked ruff check --fix --exit-zero "$file" &&
      uv run --locked ruff format "$file"
  ) >/dev/null 2>&1
done <<<"$files"

exit 0
