# Pull requests

The PR body, the title rule and stacking are the shared `pr` skill's; babysitting is the shared
`babysit-pr` skill's. This file holds what is particular to this repo.

## Title

A Conventional Commit subject in English, since the squash merge makes it the commit on `main`, with
the area in the scope when one area owns the change (`fix(scan): ...`). CI checks it
(`.github/workflows/pr-title.yml`); check it before `gh pr create` with
`uv run just pr-title "<title>"`.

## Base branch

Every PR targets `main`, unless it is a layer of a stack. Branch protection requires the branch to be
up to date with `main`, and nothing updates it on its own: the babysit does it once being behind is
all that is left. A PR nobody is babysitting is updated the same way by hand.

`.github/pull_request_template.md` is the `pr` skill's template, for a PR opened by hand.

## PRD PRs

One PR carries every sub-issue of a PRD, with the body the `pr` skill describes. It is the one PR
opened as a **draft**: the `implement-spec` skill opens it after the first slice merges and marks it
ready when the last has landed and the chain has been reviewed, then babysits it. The chain pays one
review instead of one per slice.
