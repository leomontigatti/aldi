"""Fail unless PR_TITLE is a Conventional Commit subject: `type(scope)!: subject`.

Squash merges turn the title into the commit subject on main. Run locally:
    PR_TITLE='feat(prototype): ...' python3 .github/scripts/check_pr_title.py
"""

import os
import re
import sys

TYPES = ("build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test")
PATTERN = re.compile(rf"(?P<type>{'|'.join(TYPES)})(\((?P<scope>[a-z0-9-]+)\))?!?: (?P<subject>.+)")
MAX_LENGTH = 100

title = os.environ.get("PR_TITLE", "").strip()
problems = []
match = PATTERN.fullmatch(title)
if not match:
    problems.append(f"must look like `type(scope): subject`, type one of: {', '.join(TYPES)}")
else:
    subject = match["subject"]
    if subject[0].isupper():
        problems.append("subject starts lowercase")
    if subject.endswith("."):
        problems.append("subject has no trailing period")
if len(title) > MAX_LENGTH:
    problems.append(f"at most {MAX_LENGTH} characters (has {len(title)})")

if problems:
    print(f"PR title: {title!r}")
    for problem in problems:
        print(f"  - {problem}")
    sys.exit(1)
print(f"PR title OK: {title}")
