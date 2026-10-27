# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v`; `gh` does this automatically when run inside a clone.

## Labels

The list of labels — name, colour, description — is [`.github/labels.json`](../../.github/labels.json).
That file says what they mean. Add or change a label in the JSON, run `uv run just labels-sync`, then
describe it here.

The skills speak in terms of five triage roles. Three map to a label here; the other two map to what replaced them:

| Role                       | Label in our tracker           | Meaning                                                                                                 |
| -------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------- |
| `needs-triage`             | `needs-triage`                 | Maintainer needs to evaluate this issue                                                                 |
| `needs-info`               | `question` + `ready-for-human` | Not specified enough to act on; a person has to decide first                                            |
| `ready-for-agent`          | `ready-for-agent`              | Fully specified & grabbable — triage state, **not** an AFK trigger                                      |
| `ready-for-human`          | `ready-for-human`              | Needs a human: a decision to settle or work an agent cannot do                                          |
| `wontfix`                  | none: close it                 | Close `not planned` with a `Closure:` comment ([Closing an issue](#closing-an-issue)) |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

`needs-info` and `wontfix` were retired. With one maintainer there is no reporter to wait on, so "needs more
information" is a decision the maintainer owes, which is what `question` + `ready-for-human` already say. And a
decision not to do something is made by closing: an open issue labelled "will not be actioned" is a closure
that did not happen.

### Priority and type

The roles above say whether an issue can be picked up. They do not say how much it matters. Triage also gives
every issue exactly one priority label and at least one type label, and removes `needs-triage` on the way out.
An issue with no `priority:*` label has not been triaged.

| Priority label    | Meaning                                                                                  | Test                                   |
| ----------------- | ---------------------------------------------------------------------------------------- | -------------------------------------- |
| `priority:urgent` | She cannot translate a document today, or a release broke her install                     | Would I drop what I am doing for this? |
| `priority:next`   | The short list to pull from next. Keep it to about 15                                    | Will I start this within two weeks?    |
| `priority:later`  | Worth doing, no blocker, not scheduled                                                   | Would I be sad to close it?            |
| `priority:parked` | Deliberately not now, and the issue names what wakes it                                  | Is there a condition that reopens it?  |

`priority:urgent` is empty unless she is stuck. A parked issue carries a comment stating its wake-up trigger
("she finds Gemini's Spanish worse than DeepL's"); without one it is `priority:later`.
Deciding against is a different thing: parked will be done when the trigger fires, a rejected issue is closed.

| Type label    | Meaning                                                                   |
| ------------- | ------------------------------------------------------------------------- |
| `bug`         | Wrong behaviour today                                                     |
| `enhancement` | New capability or UX                                                      |
| `refactor`    | Internal structure, no behaviour change                                   |
| `chore`       | Dependencies, tooling, CI, docs or operations                             |
| `question`    | A decision to settle rather than work to do; pairs with `ready-for-human` |

The dispatch queue is `priority:next` plus `ready-for-agent`:
`gh issue list --label priority:next --label ready-for-agent`. Skip an issue whose
`gh issue view <n> --json blockedBy` still lists an open blocker: blocking is GitHub's native relation
([Ticket operations](#ticket-operations)), and no label tracks it.

Sub-issues are not triaged on their own. `to-tickets` creates each slice with its PRD's priority, type and
milestone. That multiplies a PRD in any priority list, so add `no:parent-issue` for the PRD-level view:
`gh issue list --label priority:next --search "no:parent-issue"`.

> **`ready-for-agent` triggers nothing.** It is a triage state ("specified and grabbable"): a local session
> picks the issue up from the dispatch queue above. No label starts a run.

### Source

A `source:*` label says who opened an issue when it was not a person. `source:app` marks an **Error Report**: the
app opens it from her laptop with `bug` and `needs-triage`, and adds a comment instead of a new issue when the
same problem repeats ([ADR 4](../adr/0004-errors-reported-as-public-issues.md)). Triage it like any bug; the
report names the app version and the failed pages, never the document, so reproducing it means asking her
which document it was.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments` then keep only `authorAssociation` of `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE` (drop `OWNER`/`MEMBER`/`COLLABORATOR`).
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either: resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies**, the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only, the live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the map's sub-issues / task list), drop any with an open blocker (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or an assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me`, the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.

## PRD workflow

A PRD is one issue with a `## Problem Statement`, `## Solution`, numbered user stories, `## Implementation
Decisions`, `## Testing Decisions`, `## Out of Scope` and `## Further Notes`, written by `/to-spec` from the
conversation and labelled `ready-for-agent`. Its slices are sub-issues created by `/to-tickets`
([Ticket operations](#ticket-operations)). The PRD is closed by the PR that closes its chain, never by hand.

## Ticket operations

- **Sub-issue**: a child of the PRD (or of any parent issue) through GitHub's sub-issues. `gh api` on the
  sub-issues endpoint adds one; `gh issue view <PRD> --json subIssues` lists them.
- **Blocking**: GitHub's native issue dependencies, as in [Wayfinding operations](#wayfinding-operations):
  `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`.
  `gh issue view <n> --json blockedBy` shows the open blockers.
- **Frontier**: the open tickets whose blockers are all closed, in the order the PRD lists them.
- **Creating a slice**: `gh issue create --title ... --body ...` with the PRD's priority, type and milestone,
  and `ready-for-agent`. The body names the behaviour the slice delivers and its acceptance criteria, not
  file paths.

## Closing an issue

Close with `gh issue close <n> --comment "..."`. A closure that is a decision not to do the work starts its
comment with `Closure:` and says why; a closure that is done says what landed (a commit or a PR).

## Research tickets

A research ticket is answered by the shared `research` agent ([agent-configuration.md](./agent-configuration.md#delegating-to-subagents))
and its findings are posted as a comment with the source URL beside each claim. A finding that a later
decision will cite is also committed as `docs/research/<kebab-name>.md`, in a branch that goes through a
PR like any other change.
