# Live proof

Last completed paid proof: 2026-08-03, before the 2026-08-16 autonomy release.

The repeatable harness is `uv run coach-prove`. It uses real Terra and Sol calls,
replaces Telegram HTTP transport with an in-process transcript, and refuses any
database except its explicitly guarded local proof database.

```powershell
$env:DATABASE_URL = 'sqlite:///./.local-live-proof.db'
$env:PROOF_DATABASE_URL = $env:DATABASE_URL
uv run alembic upgrade head
uv run coach-prove
```

The harness now exercises immediate writes with no approval/resume choreography,
flexible proposal selection through toggle + Done, progressive Terra Hint/Why,
attempt recording, lesson/credit state, scheduler behavior, webhook idempotency,
and retry after Telegram failure. It also asserts the 16-turn bound.

The autonomy implementation and updated harness have automated local coverage,
but the harness has **not** been rerun against paid live models for this release.
Do not interpret prior live counts or transcripts as proof of the new behavior.

An optional guarded Telegram verifier remains available for separate test-bot
credentials. It refuses the configured production bot and sends nothing unless
`STAGING_TELEGRAM_ALLOW_SEND=YES` is explicitly set.
