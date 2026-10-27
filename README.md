<img src="src/aldi/icon.svg" width="64" height="64" alt="">

# aldi

Translates English or Portuguese PDFs into Spanish on your own laptop (Windows or macOS), so they can be read,
highlighted and edited. A born-digital PDF can keep its original layout; a scanned one becomes a clean reading
version, as PDF and DOCX, with its figures and the original page numbers. It runs on Gemini's free tier with your
own key.

Install it from the latest release with one line, then open Aldi from the desktop shortcut (Windows) or from
Applications (macOS). The line asks for the token the app files its error reports with.

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://github.com/leomontigatti/aldi/releases/latest/download/install.ps1 | iex"
```

```sh
curl -LsSf https://github.com/leomontigatti/aldi/releases/latest/download/install.sh | sh
```

- How it is built and why: [`GLOSSARY.md`](GLOSSARY.md) and [`docs/adr/`](docs/adr/).
- Working on it: [`AGENTS.md`](AGENTS.md) and [`docs/agents/validation.md`](docs/agents/validation.md).
