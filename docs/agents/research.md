# Research sources

What the shared `research` agent prefers for this stack: Python 3.12, Gemini's API, `pdf2zh-next`, Typst and
Pandoc, packaged for Windows and macOS with `uv`.

## Pinned versions

`pyproject.toml` with `uv.lock`: read a dependency at the version the lock pins, not at its branch head.

## Primary sources

- Gemini API: the docs at `ai.google.dev/gemini-api/docs` (models, rate limits, structured output) and the
  `googleapis/python-genai` repo. Free-tier limits change without notice: a 429's error body names the quota and
  its value, and beats any page that lists them.
- `pdf2zh-next`: the `PDFMathTranslate-next/PDFMathTranslate-next` repo and its docs; `funstory-ai/BabelDOC` for
  the layout engine underneath.
- Typst (`typst/typst`, and the `typst` package on PyPI), Pandoc (`jgm/pandoc`, `pypandoc-binary` on PyPI),
  pywebview (`r0x0r/pywebview`).
- `uv`: `docs.astral.sh/uv`, for installing tools and Python on Windows and macOS.
- PyPI's JSON API for a package's versions, wheels per platform and `requires_python`.

A finding a later decision will cite goes in `docs/research/<kebab-name>.md`, as
[issue-tracker.md](issue-tracker.md#research-tickets) says.
