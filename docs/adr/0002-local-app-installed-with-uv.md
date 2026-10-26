# 2. A local app installed with uv, not a server or a packaged executable

Date: 2026-10-26. Status: accepted.

## Context

She translates a document once in a while, on a Windows laptop; macOS should work too. Considered: a Telegram
bot on the `rylai` VPS (2 CPUs, shared with En Escena production; Telegram's hosted bot API caps downloads at
20 MB, so a self-hosted bot API server), a hosted web app, a packaged `.exe`/`.dmg` (PyInstaller, Briefcase,
Flet, Tauri or Electron with a Python sidecar), and a Python app installed with `uv`.

With Gemini doing the reading and translating ([ADR 1](0001-gemini-free-tier-only.md)), the laptop only
orchestrates, so a server buys nothing. A packaged app weighs 0.5 to 1 GB per update (the layout model, Pandoc,
Typst), is built per system, and unsigned it trips SmartScreen and Gatekeeper; signing costs money every year.

## Decision

A Python 3.12 package, installed once with `uv` from this public repo by one PowerShell (or shell) line. A
desktop launcher (a Windows shortcut, a macOS `.app`) opens a native window through pywebview over a local web
UI; nothing listens beyond `localhost`. An update button installs the latest release tag. Every dependency is
pinned in `uv.lock`, and CI runs the tests on Windows, macOS and Linux.

## Consequences

- No hosting, no bot, nothing exposed to the internet.
- Someone runs the install once on her laptop; the key lives there, not in the repo.
- A release tag reaches her laptop on the next update: tagging is a one-way door ([review.md](../agents/review.md)).
- Pins matter: `pdf2zh-next` 2.9.0 broke on `bitstring` 5 in the spike.
