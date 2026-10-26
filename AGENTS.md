# Aldi — guide for coding agents

Routes to the repo's conventions; each rule lives in the file a route names. The first section applies to
every task; a line in the second applies when its leading word matches the task.

Aldi turns an English or Portuguese PDF into a Spanish one, on the user's own laptop (Windows first, macOS
too). A **Born-digital PDF** keeps its layout through `pdf2zh-next`; a **Scanned PDF** is read and translated
by Gemini a few pages per request and rebuilt as a **Reading Version** with its figures. Both run on Gemini's
free tier only. The terms are in [GLOSSARY.md](GLOSSARY.md); the decisions in `docs/adr/`. The code is a
Python package in `src/aldi/`, its tests in `tests/`.

The skills, agent briefs and shared hooks are the shared workflow in `leomontigatti/agent-workflow`, installed at
`~/.agents/workflow`; this repo keeps only its own facts, in the files its `contract.md` lists, and its own hooks
in `.agents/hooks/` ([agent-configuration.md](docs/agents/agent-configuration.md)). In Codex, "call the Skill tool
with X" means open `~/.agents/workflow/skills/X/SKILL.md` and follow it.

The contract files this repo keeps: this file, [GLOSSARY.md](GLOSSARY.md), `t3.json`, `.agents/required-tools`,
`docs/adr/`, and in `docs/agents/`: [validation.md](docs/agents/validation.md),
[verification.md](docs/agents/verification.md), [review.md](docs/agents/review.md),
[issue-tracker.md](docs/agents/issue-tracker.md), [pull-requests.md](docs/agents/pull-requests.md),
[t3-dependencies.md](docs/agents/t3-dependencies.md) and [research.md](docs/agents/research.md).

## Every task

- **Session conduct**: when a step needs no input from the user, keep going, and put status notes in the same
  message as the next action. Stop and ask only when the work cannot continue without them, or before anything
  destructive or outside this repository: deleting data, force-pushing, tagging a release (her laptop updates
  from tags). When a rule in these docs fights the task, say so and get a sign-off before breaking it. End a run
  with what needs the user first, then what changed, then what was found in this run.
- **Implementing** (a feature, a fix, any code change): call the Skill tool with "implement" before editing. Where
  work starts and what this repo's worktrees need: [workflows.md](docs/agents/workflows.md); the thread's branch,
  worktree and PR: the shared `implement` skill's `THREADS.md`.
- **Commands**: run only the recipes listed in the `justfile`, as `uv run just <recipe>`. [validation.md](docs/agents/validation.md)
  is the whole surface. A command the task needs that is not a recipe means adding the recipe first, not running it
  ad hoc.
- **Her documents** live only in the git-ignored `samples/`: copyrighted books and journal articles. Tests build
  their own small PDFs; nothing from `samples/`, nor an output made from it, is committed or quoted at length in an
  issue or PR.
- **Gemini quota**: the tests never call Gemini (they use a fake at the translator seam; the fake and the builders of test PDFs live in
  `tests/conftest.py`). A run against a real
  document spends the free quota of the key in `.env`; run one only when the change needs it, on the fewest pages
  that prove it.
- **Subagents**: each role's model, and how a session reaches it, is the shared `agents/models.md`; when to
  delegate at all: [agent-configuration.md](docs/agents/agent-configuration.md#delegating-to-subagents).
- **Code language**: English in code, commits, PRs and docs; Spanish everywhere the user reads it (every
  window, button, progress line and error), and as data inside backticks.
- **Cross-platform**: the app runs on Windows and macOS. Paths through `pathlib`, text files opened with
  `encoding="utf-8"`, no shell-outs to tools a fresh Windows laptop lacks; CI runs the tests on all three
  systems.
- **Coding standards**: ruff (`pyproject.toml`) enforces style and imports; the rest are these docs and
  `GLOSSARY.md`.

## When routed

- **Commands and the validation gate**: [validation.md](docs/agents/validation.md); `uv run just gate` before a
  commit is done.
- **What a user sees** (the app window, the PDF and DOCX it writes): [verification.md](docs/agents/verification.md).
- **Documentation**: [workflows.md](docs/agents/workflows.md#documentation).
- **Review** (tiers, always-ask areas, hotspots, one-way doors): [review.md](docs/agents/review.md).
- **Research** outside the repo: [research.md](docs/agents/research.md).
- **Pull requests**: [pull-requests.md](docs/agents/pull-requests.md).
- **Issues**, **PRDs**, **tickets**, **triage labels**: [issue-tracker.md](docs/agents/issue-tracker.md).
- **Domain docs**: [domain.md](docs/agents/domain.md): `GLOSSARY.md` and `docs/adr/`.
- **Agent configuration** (the shared workflow, project hooks, PATH):
  [agent-configuration.md](docs/agents/agent-configuration.md). A change to a shared skill is a PR to
  `leomontigatti/agent-workflow`, never a copy here.
- **T3 Code dependencies** (a change to a workflow of this repo that leans on a T3 tool, setting or `t3.json`):
  [t3-dependencies.md](docs/agents/t3-dependencies.md); keep its entry's "Used by" true.
