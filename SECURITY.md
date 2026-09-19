# Security

## Reporting a vulnerability

Please use GitHub's private reporting: **Security → Report a vulnerability** on this repository.
Do not open a public issue for security problems. This is a small volunteer project: expect a first reply
within about a week.

## What this software does on your machine (so you can judge the risk)

- Adds **hooks** to Claude Code's `settings.json` (`token-history`, `rtk`) that run on every prompt or Bash call.
  Every hook written here fails open (does nothing) on error.
- `token-dashboard` runs a **read-only local web server on 127.0.0.1**, rejects foreign `Host` headers, sends no
  data anywhere and serves a page with a strict Content-Security-Policy.
- `token-history` stores chat text in `~/.claude/token-history/history.db` (owner-only). Secret redaction is
  **best effort**: it removes common key formats and drops short credential-related messages, but it cannot
  guarantee that no secret is stored. Use `token-history forget` to delete data.
- Files in a shared team folder are treated as untrusted input (type-checked, size-limited, shown as plain text).
- The installers download third-party software (see THIRD_PARTY.md). rtk's installer verifies release checksums.

## Known limits

- Indexed chat text could contain instructions that a model then reads back via auto-recall; recalled notes are
  labelled as reference data and kept short, which reduces but does not remove this risk.
- The PowerShell installer is exercised only by CI dry runs, not by real installs on every Windows setup.
