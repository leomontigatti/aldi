# T3 Code dependencies

What this repo's own workflows assume about T3 Code, beyond what the shared workflow's
`t3-dependencies.md` (in `leomontigatti/agent-workflow`, installed at `~/.agents/workflow`)
records for the shared skills. The `t3-update-review` skill reads both after every T3 update;
agents don't need this file for everyday work.

Written against the T3 Code installed on 2026-10-26; the entries reuse what `pcp`'s file checked
in T3's source and do not cite it again.

Status of an entry:
- **active**: we rely on T3 behaving this way; an update that changes it breaks us.
- **watching**: a workaround for something T3 can't do (yet), or a workflow T3
  could do natively better, faster or cheaper. "Would improve if" is the exact
  condition that lets us simplify.
- **ready**: the condition is met in the installed T3, but we haven't adopted it yet.
- **resolved**: adopted; kept as history.

## Active

### setup-links-env
- **Relies on:** the `runOnWorktreeCreate` script runs with the new worktree as its cwd and
  `T3CODE_PROJECT_ROOT` set to the main checkout, so it can link `.env` and `samples/` from there.
  In the main checkout (a thread launched as `existing_worktree` on the project root) the script
  compares the two paths and only syncs. It runs `uv sync --locked`, so `uv` must be on the setup
  terminal's PATH.
- **Used by:** `t3.json`; `docs/agents/workflows.md` § This repo's worktrees.
- **Breaks if:** T3 changes the script's cwd, stops setting `T3CODE_PROJECT_ROOT`, or runs the
  setup terminal without the login session's PATH.

### cleanup-unlinks-env
- **Relies on:** T3's storage cleanup removes a worktree with plain `git worktree remove`, which
  unlinks the `.env` and `samples` symlinks without following them, so the main checkout's key and
  her documents survive.
- **Used by:** `t3.json` (the symlinks it creates); `docs/agents/workflows.md` § This repo's
  worktrees.
- **Breaks if:** cleanup starts following symlinks, or deletes the files a symlink points at.

### project-hooks-worktree-dir
- **Relies on:** the sessions T3 starts load this repo's own hooks (`.claude/settings.json`,
  `.codex/hooks.json`) with the thread's worktree as the project directory, so the Stop hook lints
  that worktree's changes. Not checked in T3 source.
- **Used by:** `.claude/settings.json`; `.codex/hooks.json`; `.agents/hooks/stop-lint-check.sh`;
  `docs/agents/agent-configuration.md` § Project hooks.
- **Breaks if:** T3 starts sessions in a directory other than the thread's worktree, or without a
  project directory variable for the hooks to resolve.

## Watching

### preview-file-upload
- **Workaround:** none yet. `docs/agents/verification.md` § Drive drops a PDF into the app with
  `preview_upload`, which has not been tried against the app's drop zone.
- **Used by:** `docs/agents/verification.md` § Drive.
- **Would improve if:** the first ticket that adds the app window drives an upload with
  `preview_upload` and it works; then this entry is resolved, or § Drive names the fallback.
- **Watch:** `preview_upload`.

## Ready

None yet.

## Resolved

None yet.
