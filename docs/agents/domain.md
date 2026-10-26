# Domain docs

How this repo's domain documentation is laid out and read. The shared `domain-modeling` skill writes it.

## Layout

One context, so one glossary:

```
/
├── GLOSSARY.md      ← the terms
├── docs/adr/        ← the decisions
└── src/aldi/
```

Before exploring an area, read `GLOSSARY.md` and the ADRs in `docs/adr/` that touch it.

## The glossary

Each term has an English name, used in discussion and in every code identifier, and a Spanish translation
(`_Spanish_:`), the words the app shows her. When your output names a domain concept (an issue title, a test
name, a hypothesis), use the glossary's term, not a synonym it lists under `_Avoid_`. New entries keep this shape,
the `_Spanish_:` line included, rather than the `ui:` line of the shared skill's `GLOSSARY-FORMAT.md`; a term she
never sees says `(not shown)`. A concept the glossary lacks is either language the project does not use, or a gap
for `domain-modeling`.

## Stress-testing a term

Name the kind of document in every scenario: a **Born-digital PDF** or a **Scanned PDF**, English or Portuguese,
and what is on the page (two columns, a photo grid with one caption, a table, references). The hard cases are the
book's: low-resolution scans, photo-only pages, captions beside the figure, scanner artifacts.

## ADR conflicts

If your output contradicts an existing ADR, say so explicitly rather than silently overriding it:

> _Contradicts ADR-0001 (…), but worth reopening because…_
