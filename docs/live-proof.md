# Agentic V2 live proof

Date: 2026-08-03

The repeatable live acceptance harness is
`uv run coach-v2-prove`. It uses the configured real OpenAI API key and real
`gpt-5.6-terra` / `gpt-5.6-sol` models. Only Telegram HTTP transport is replaced
with an in-process transcript. The suite has passed against both the disposable
SQLite database and PostgreSQL 16.14 in an isolated local Podman container.

```powershell
$env:DATABASE_URL = 'sqlite:///./leetcode-coach-service/.local-v2-live-proof.db'
$env:V2_PROOF_DATABASE_URL = $env:DATABASE_URL
uv run alembic -c alembic-v2.ini upgrade head
uv run coach-v2-prove
```

For the PostgreSQL proof, start an isolated `postgres:16-alpine` container on
`127.0.0.1:55432`, migrate it, set `V2_PROOF_ALLOW_POSTGRES=1`, and use the
guarded proof DSN documented by `prove_live_flows.py`.

The clean run completed with `LIVE_PROOF_OK` and covered:

- canonical five-problem proposals, deterministic HTML, and 3-medium/2-hard mix;
- read-only status and canonical House Robber difficulty;
- natural-language picks, persisted approval, process restart, exact `yes`, and review delivery;
- Java coaching, correctness/invariant/complexity feedback, approved attempt, lesson, and credits;
- Hint, Why, Skip, Saw Solution, Reattempt, two-button Pick, Solve Now, and Snooze;
- a rejected lesson write, exact `no`, and a harmless stale approval callback;
- one real read-only Sol escalation followed by continued Terra control;
- scheduler refill, expiry-generated Extend button, extension, idempotent daily tax,
  debt nudge, Accept Deficit, and a bounded canonical refresh fixture;
- real ASGI webhook delivery, duplicate `update_id`, forced Telegram delivery
  failure returning HTTP 503, and successful retry without duplicate domain writes;
- invented-slug rejection without inserting or displaying fabricated metadata.

Persisted-state audit after the run:

```text
17 agent runs; maximum 5 turns; maximum 1 Sol call per run
43 tool calls; cache reads 379529 tokens; cache writes 24023 tokens
2 canonical five-problem batches, both 3 medium / 2 hard
1 attempt; 1 lesson; 5 reviews; 5 credit entries
2 approved and 2 rejected approvals; no pending approvals
processed update_ids 777, 880, and 881 all handled
House Robber = easy; invented slug absent
```

The full automated suite passed: `228 passed`. Request-level Bot API tests verify
send, HTML/buttons, edit, callback acknowledgement, webhook secret registration,
Telegram's post-entity 4,096-character limit, 64-byte callback limit, HTTPS and
secret-token constraints, visible hard failures, and three-attempt transient retry.
Crash-recovery regressions also verify the update claim lease, in-flight 503
responses, pending-approval serialization, superseded alias expiry, harmless
stale callbacks, bounded read-tool projections, and unbounded opaque SDK state storage.
The real PostgreSQL run verified
all Telegram identifiers as `BIGINT`/`BIGSERIAL`, including the configured
REDACTED_CHAT_ID chat ID, opaque run state as `TEXT`, and zero leaked advisory
locks after cross-engine webhook retries. A two-engine scheduler-leader test
also proved only one process can hold the scheduler lock and that disposal
releases it. A real Uvicorn process also
started against the proof database and returned
`{"status":"ok","database":"up"}` from `/health`.

## Telegram boundary

The configured Telegram token passes read-only `getMe` and `getWebhookInfo`
checks. Its existing webhook has zero pending updates; its last recorded error
is an old TLS handshake failure from 2026-07-30. No message was sent and no
webhook was changed because this is the existing bot. Per the current delivery
scope, a separate staging run is not required; local request-level contract
tests and the ASGI webhook simulator are the acceptance gate.

An optional guarded outbound verifier remains available if separate test-bot
credentials are supplied later:

```powershell
$env:V2_STAGING_TELEGRAM_BOT_TOKEN = '<separate staging token>'
$env:V2_STAGING_TELEGRAM_CHAT_ID = '<staging chat id>'
$env:V2_STAGING_TELEGRAM_ALLOW_SEND = 'YES'
uv run coach-v2-prove-telegram
```

It refuses the configured existing bot token and performs no send without the
exact authorization variable. It verifies Bot API identity/chat access, plain
text send, edit, deterministic escaped HTML, and inline-button delivery.
