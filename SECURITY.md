# Security Policy

## Supported versions

This is a single-maintainer hobby project. Only the latest `main` branch
receives security fixes. There are no backport branches or LTS releases.

## Reporting a vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, use GitHub's private vulnerability reporting:

1. Go to **https://github.com/DurdeuVlad/leetcode-coach-service/security/advisories/new**
2. Fill in the advisory form (what you found, impact, repro steps).
3. The maintainer is notified privately and will respond within 7 days.

If GitHub private advisories are unavailable to you, email the maintainer
directly (see the GitHub profile for contact info) with the subject
`[SECURITY] leetcode-coach-service`.

Please **do not** disclose the vulnerability publicly until a fix has been
released and you've been given the all-clear.

## What counts as a security issue here

This service integrates with several external APIs (Telegram, OpenAI, Google
Gemini, LeetCode, YouTube) and handles API keys. Examples of
real security issues:

- A way to leak `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`,
  `GEMINI_API_KEY`, or any other secret via logs, error messages,
  HTTP responses, or the `/health` endpoint.
- A way to bypass the Telegram chat allowlist (NFR-4) and trigger Flow B
  actions from a non-allowlisted chat.
- SSRF, SQL injection, or command injection in any client or route.
- A way to forge the `X-Telegram-Bot-Api-Secret-Token` webhook
  authentication check.
- Auth bypass in any integration client.

## What does NOT count (file a normal issue instead)

- LeetCode's GraphQL API being undocumented / rate-limiting / blocking your
  IP — that's LeetCode's service, not a vuln in this code.
- A third-party dependency having a CVE — run `uv pip audit` and open a
  normal issue with the CVE id and affected version; we'll bump the pin.
- The single-user scope being "insecure" by design (no multi-tenant
  isolation) — this is documented in `docs/business-requirements.md` §7
  and `docs/architecture.md` §12. It's intentional, not a bug.
- Anything that requires already having the operator's secrets to exploit
  (no privilege escalation, no impact without prior compromise).

## Secret handling expectations

- All secrets are env vars (see `.env.example` for the key list). No
  secrets are committed to the repo — `.gitignore` blocks `.env` and
  `.env.*` (except `.env.example`, which has keys only, never values).
- If you find a real secret committed in git history, **do not open a
  public issue** — use the private advisory flow above so the maintainer
  can rotate the secret and rewrite history before public disclosure.
- The `Dockerfile` runs as a non-root user; the `docker-compose.yml` does
  not expose the Postgres port to the host by default. Don't weaken these
  in a PR without an explicit security justification.

## Disclosure policy

- **Acknowledgement:** within 7 days of private report.
- **Fix or mitigation:** target 30 days for high-severity, 90 days for
  low-severity. The maintainer will coordinate a disclosure date with you.
- **Credit:** reporters are credited in the GitHub Security Advisory and
  the release notes unless they prefer to remain anonymous.
