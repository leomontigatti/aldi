#!/usr/bin/env bash
#
# Stop hook, blocking. Wired from both `.claude/settings.json` and `.codex/hooks.json`.
#
# A turn may not end on a red lint of the files it changed. Aldi's lint is pre-commit, and its
# hooks run per file, so this gate runs them on the changed files only: `pre-commit run --files`
# over the uncommitted working tree.
#
# It does not run the tests: `uv run just gate` is the check before a
# commit is done, and the agent runs it.
#
# It exits early on the three paths where it buys nothing:
#
# - `SKIP_STOP_CHECKS` set: the documented local bypass.
# - `GITHUB_WORKFLOW` set: a workflow authors no code here, so the gate is a local-session concern.
# - No changed path that a pre-commit hook reads: the filter is the union of the file types the
#   hooks in `.pre-commit-config.yaml` match (`.py`, `.sh`, `.toml`, `.yml`/`.yaml`, `.json`).
#   Keep the two in step when a hook changes its types.
#
# On failure it exits 2 with the failing output on stderr, which both harnesses feed back to the
# agent. It keeps blocking while the failure persists; Claude Code force-closes the turn after 8
# consecutive blocks, which is the backstop, and that is why `stop_hook_active` only picks the
# trailing instruction.
#
# See docs/agents/agent-configuration.md § Project hooks.

set -uo pipefail

input="$(cat)"

if [ -n "${SKIP_STOP_CHECKS:-}" ]; then
  exit 0
fi

if [ -n "${GITHUB_WORKFLOW:-}" ]; then
  exit 0
fi

# Every failure below is fail-open on purpose: this hook blocks the end of a turn, so a broken
# project dir or a non-repo checkout must not wedge the session. That is also why `set -e` is
# absent.
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" || exit 0
command -v uv >/dev/null 2>&1 || exit 0

# `-z` so paths arrive unquoted. An entry is `XY path`; a rename or copy is followed by its origin
# path as a separate entry, which is skipped. `--untracked-files=all` lists files inside new
# directories, not the directory.
changed=()
mapfile -d '' entries < <(git status --porcelain --untracked-files=all -z 2>/dev/null)
for ((i = 0; i < ${#entries[@]}; i++)); do
  entry="${entries[i]}"
  [ -n "$entry" ] || continue
  status="${entry:0:2}"
  path="${entry:3}"
  case "$status" in
    R* | C*) i=$((i + 1)) ;;
  esac
  [ -f "$path" ] || continue
  if printf '%s\n' "$path" | grep -Eq '\.(py|sh|toml|ya?ml|json)$'; then
    changed+=("$path")
  fi
done

if [ "${#changed[@]}" -eq 0 ]; then
  exit 0
fi

if output="$(uv run --locked pre-commit run --files "${changed[@]}" 2>&1)"; then
  exit 0
fi

# Drop the hooks that passed, as `uv run just gate` does; keep the failing ones whole.
shown="$(grep -vE '\.\.\.+(Passed|\(no files to check\)Skipped)$' <<<"$output")"

if [ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ]; then
  instruction="If you cannot fix this, say so explicitly and report the work as blocked."
else
  instruction="Fix this before ending the turn."
fi

printf '%s\n\n%s\n' "$shown" "$instruction" >&2
exit 2
