# Agent configuration

How Claude Code and Codex get this repo's instructions, the shared workflow and the project's own
hooks, and how a session delegates to a subagent.

## The shared workflow

The skills, the agent briefs (`implementer`, `research`, `reviewer`) and the hooks that depend on
no project live in `leomontigatti/agent-workflow`, installed at `~/.agents/workflow`. Its
`install.sh` links them where Claude Code and Codex read them at user level, so this repo holds no
copy. Change a shared skill there, through a PR; a skill only this repo would use goes in
`.agents/skills/<name>/`, under a name no shared skill has.

The shared skills read this repo only through the files the shared `contract.md` lists; the ones
this repo keeps are named in `AGENTS.md`.

## One copy, two harnesses

- **`AGENTS.md` is the only instructions file**, here and in every parent directory. Claude Code
  reads `AGENTS.md` (2.1.277+) only as a fallback, when no `CLAUDE.md`, `.claude/CLAUDE.md` or
  `CLAUDE.local.md` sits in the working directory or above it, so adding one would silently stop it
  reading these instructions. Claude-only notes go in `~/.claude/CLAUDE.md`, which does not count.
- **The project hooks** live in `.agents/hooks/` and are wired from both `.claude/settings.json` and
  `.codex/hooks.json`, which carry the same events and matchers (Codex accepts `Write|Edit` for its
  `apply_patch` tool). The scripts read either payload: Claude Code's `tool_input.file_path` or a
  Codex patch in `tool_input.command`, and `$CLAUDE_PROJECT_DIR` or, when Codex leaves it unset,
  the checkout the hook runs in. The shared hooks are wired at user level by `install.sh`.
- **Codex reads `.codex/` from the main checkout, even in a worktree**: a T3 worktree gets the hooks
  only once they are on the branch the main checkout has out, while `AGENTS.md` comes from the
  worktree itself. The hook commands resolve `git rev-parse --show-toplevel`, so the scripts that
  run are the worktree's.
- **Codex runs a project hook only once it is trusted**: the project must be trusted, and each hook
  reviewed and approved when Codex first offers it.
- **Harness-only files stay where they are**: the permission allowlist in `.claude/settings.json`
  has no Codex counterpart.

## Project hooks

Hooks are configuration, not a terminal feature, so they run in a headless session too. They read
JSON on stdin and need `jq`.

- **`PostToolUse` on `Write|Edit`**: `format-edited-file.sh` runs `ruff format` and
  `ruff check --fix --exit-zero` over each edited `.py` file, silently, so formatting is not a
  session's to remember.
- **`Stop`**: `stop-lint-check.sh` runs `pre-commit run --files` on the changed `.py`,
  `.sh`, `.toml`, YAML and JSON files of the uncommitted working tree, and **blocks** with exit 2
  when one fails, putting the output in front of the agent. It does not run the tests: that is
  `uv run just gate`, which the agent runs before a commit is done.

`SKIP_STOP_CHECKS` set to any value is the local bypass (`SKIP_STOP_CHECKS=1 claude`, or `codex`,
or exported for the session). The gate keeps blocking while the failure persists; Claude Code
force-closes the turn after 8 consecutive blocks, so the wording of the last instruction is all
`stop_hook_active` decides.

## The agent shell's PATH

The shared `check-tool-path` hook warns at the start of a session whose shell cannot find a tool
the work needs: the shared ones, plus `uv` from `.agents/required-tools`. `uv` lives
in `~/.local/bin`, which is the line this machine needs in `~/.profile` (the desktop login session
reads it, not `~/.bashrc`):

```sh
export PATH="$HOME/.local/bin:$PATH"
```

`gh` is a system binary here. A change to `~/.profile` takes effect at the next login,
not in a running T3 Code. When the warning fires, the fix is that profile line, made by the user; a
session does not prefix its commands with `export PATH=…`.

## Delegating to subagents

A subagent costs a full orientation and returns only its summary, so delegate by the size of the
read, not the size of the task:

- Reading one to three files to decide or verify something is inline work.
- Understanding that needs four or more files is one narrow mapping subagent, which returns the
  conclusion, not the file dumps.
- Bulk output (a long diff, a log, a rendered page) is read by a subagent when only its conclusion
  is needed in the main thread.
- Bash for state (`git`, `gh`) stays inline.

Brief a subagent with the exact paths and skill files to read, never a digest of them, and ask for
its report as text with a fixed shape: status, one-line summary, artifacts touched, next step, risks.
A subagent whose last action is a tool call returns the tool result instead of its report.

Which model each role runs on, and how a session reaches it (reviewers, `research`, the
`implementer`), is the shared `agents/models.md`; the briefs are the shared `agents/`. Reading
outside the repo goes to the `research` agent, which reads [research.md](research.md) first.
