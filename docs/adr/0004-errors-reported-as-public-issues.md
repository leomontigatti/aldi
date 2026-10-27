# 4. Errors are reported automatically as public GitHub issues

Date: 2026-10-27. Status: accepted.

## Context

She uses the app alone, once in a while, and is not going to describe a stack trace. The user maintains it from
elsewhere and needs to learn about a failure without her doing anything. Considered: asking her each time before
sending, an email from the app, a log file she sends by hand, a crash service (Sentry and the like, which is a
new account and a third party holding the data), and an issue on this repo's tracker. The repo is public.

## Decision

When something goes wrong the app opens an **Error Report** as an issue on `leomontigatti/aldi`, without asking,
and tells her only `Algo salió mal. Ya se envió un informe para que lo arreglen.`

- **Reported**: a crash, a **Failed Page** (a page that still fails after its retries), a failed install or
  update, and a result that cannot be written. **Not reported**, because they are not bugs: a **Quota Pause**, no
  internet, an invalid key, Gemini overloaded (`503`).
- **Contents**: the app version, the system, the **Output Mode**, the kind of document, its page count, the failed
  pages, the stack trace with paths and the user name removed, the Gemini model and its error. **Never** text from
  the document, its file name or the key.
- **One issue per problem**: a fingerprint of the error finds an open issue for the same problem, and a repeat
  becomes a comment on it. A daily cap stops a loop from flooding the tracker.
- **Labels**: `bug`, `needs-triage`, `source:app`.
- **The token** is a fine-grained GitHub token that can only write issues on this repo, entered once at install
  and kept beside the Gemini key. With no token, or offline, reports wait in a queue and go out later. Before the
  token expires the app opens an issue for the user (not for her) to renew it.

## Consequences

- Anything in a report is public, so redaction is a correctness requirement: a report that leaks document text,
  a file name or the key is a one-way door ([review.md](../agents/review.md)).
- A token leaked from her laptop lets someone write issues on this repo, nothing else.
- The tracker's triage covers app reports too: `source:app` tells them apart from issues a person wrote.
