# Architecture — LeetCode Coach Service

Status: design | Owner: Vlad | Last revised: 2026-07-26
Companion to `business-requirements.md` (the contract) and `roadmap.md` (the plan).

## 1. Why a Python service, not n8n

The n8n version (`n8n-reference/`) was a useful spec vehicle but is the wrong
runtime for an always-on single-user automation:

- **Visual editor advantage doesn't apply** — the workflows were edited as JSON
  text in a terminal, getting the pain of code without the power of code.
- **Documented n8n bugs in exactly the features this system depends on** —
  Fallback Model + Retry On Fail interaction loops indefinitely (n8n #18797);
  Fallback Model required even when unwanted in older versions (#17140).
- **Error handling the README asks for** (typed `invalid_grant` branch, no
  "log with estimated defaults") is fiddly per-node in n8n and trivial in
  Python with `except GoogleAuthError`.
- **The Data Table is vendor lock-in** for 4 simple relational shapes.
- **No tests, no type checking, no real diffs** in n8n JSON.

The spec work is already done — `n8n-reference/README.md` is a complete
behavioral spec, the prompts are written, the schema is fixed. The port is
mechanical.

## 2. Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12+ | matches the user's strongest language; best LLM SDK support |
| Web framework | FastAPI | needed for the Telegram webhook endpoint; thin and async |
| Scheduler | APScheduler (AsyncIO scheduler) | daily 09:05 + 05:05 cron; in-process, no extra container |
| Telegram | `python-telegram-bot` v21+ | handles webhook setup, update parsing, typed `Update` objects |
| LLM | OpenAI SDK + Google GenAI SDK | primary `gpt-5.6-sol`, fallback `gemini-3.6-flash`; explicit fallback logic (no n8n Fallback Model bugs) |
| Google Tasks | `httpx` + google-api-python-client | only needs create + update; OAuth2 refresh-token flow |
| DB | PostgreSQL (Coolify-managed) | relational, portable, queryable; one connection string |
| DB layer | SQLModel (SQLAlchemy + Pydantic) | typed models, same types in API + DB; migrations via Alembic |
| HTTP | `httpx` | async, used for LeetCode GraphQL + Google Tasks + YouTube |
| Retries | `tenacity` | typed retry policies per call site |
| Config | `pydantic-settings` | env-var backed, typed, fails fast on missing secrets |
| Tests | pytest + pytest-asyncio + respx | mocked LLM/HTTP for the coach pass; real Postgres via testcontainers for DB |
| Packaging | `uv` + `pyproject.toml` | fast, modern, single-tool |
| Container | Docker (single image) | deployed on Coolify |

## 3. Repository layout

```
leetcode-coach-service/
├── README.md                    # project overview, doc index
├── pyproject.toml               # deps, scripts, ruff config
├── Dockerfile                   # single-container build
├── docker-compose.yml           # local dev: app + postgres
├── .env.example                 # all required env vars (no values)
├── alembic/                     # DB migrations
│   ├── env.py
│   └── versions/
├── src/
│   └── leetcode_coach/
│       ├── __init__.py
│       ├── main.py              # FastAPI app + lifespan + scheduler setup
│       ├── config.py            # pydantic-settings: env vars
│       ├── db/
│       │   ├── __init__.py
│       │   ├── base.py          # SQLModel engine, session factory
│       │   └── models.py        # 4 tables (Problem, Log, PendingReview, Lesson)
│       ├── integrations/
│       │   ├── __init__.py
│       │   ├── telegram.py      # webhook setup, send_message, send_reply
│       │   ├── llm.py           # OpenAI primary + Gemini fallback, typed responses
│       │   ├── google_tasks.py  # create, update, mark complete; invalid_grant handling
│       │   ├── leetcode.py      # GraphQL pull (weekly refresh)
│       │   └── youtube.py       # YouTube Data API search (optional, v1)
│       ├── flows/
│       │   ├── __init__.py
│       │   ├── flow_a.py        # propose_5() — daily candidates
│       │   ├── flow_b.py        # handle_update() — reply router + coach pass
│       │   └── expiry.py        # sweep_expired() — 05:05 sweep
│       ├── prompts/
│       │   ├── __init__.py
│       │   ├── propose.py       # Flow A system + user prompt (ported verbatim)
│       │   └── coach.py         # Flow B system + user prompt (ported verbatim)
│       ├── scheduling/
│       │   ├── __init__.py
│       │   └── cron.py          # APScheduler job definitions
│       ├── errors.py            # typed exceptions + global alert handler
│       └── webhooks/
│           ├── __init__.py
│           └── telegram.py      # POST /telegram/webhook → flow_b.handle_update
├── tests/
│   ├── conftest.py
│   ├── test_flow_a.py
│   ├── test_flow_b.py
│   ├── test_expiry.py
│   ├── test_llm_fallback.py
│   └── test_google_auth_branch.py
├── docs/
│   ├── business-requirements.md
│   ├── architecture.md          # this file
│   └── roadmap.md
└── n8n-reference/               # frozen n8n v3 spec + JSON, for reference only
    ├── README.md
    ├── nodes/
    └── workflows/
```

## 4. Runtime topology

```mermaid
flowchart LR
    TG["Telegram<br/>webhook POST"] --> APP["FastAPI app<br/>:8000"]
    SCH["APScheduler<br/>in-process"] --> APP
    APP --> DB[("Postgres<br/>Coolify")]
    APP --> LLM["OpenAI<br/>primary"]
    APP --> LLMF["Gemini<br/>fallback"]
    APP --> GT["Google Tasks<br/>REST"]
    APP --> LC["LeetCode<br/>GraphQL"]
    APP --> YT["YouTube Data API<br/>optional"]
    APP --> TG_OUT["Telegram<br/>sendMessage"]
    ERR["global error handler"] --> TG_OUT
    APP -.raises.-> ERR
    BL["Browserless<br/>(homelab)"] -. fallback .-> LC
    SX["SearXNG<br/>(homelab)"] -. fallback .-> YT
```

- **One container.** APScheduler runs in-process inside the FastAPI app — no
  separate scheduler container. The app's lifespan handler starts/stops the
  scheduler on startup/shutdown.
- **Telegram webhook** is the only inbound HTTP surface. Endpoint:
  `POST /telegram/webhook`. Telegram sends updates there; the handler
  dispatches to `flow_b.handle_update(update)`.
- **Cron jobs** are APScheduler `CronTrigger` jobs:
  - `flow_a.propose_5()` at `5 9 * * *` Europe/Bucharest.
  - `expiry.sweep_expired()` at `5 5 * * *` Europe/Bucharest.
  - `leetcode.refresh_pool()` at `0 3 * * 1` (weekly Monday 03:00).

## 5. LLM client design (the part n8n couldn't do reliably)

```python
# src/leetcode_coach/integrations/llm.py (sketch)
class LLMClient:
    def __init__(self, primary: OpenAI, fallback: GoogleGenAI):
        self.primary = primary
        self.fallback = fallback

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
        retry_error_callback=lambda r: r.out,  # surface the last exception
    )
    async def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        try:
            return await self._call_primary(system, user, max_tokens)
        except (httpx.HTTPStatusError, OpenAIAuthError, OpenAIRateLimitError) as e:
            log.warning("primary LLM failed, falling back", error=str(e))
            return await self._call_fallback(system, user, max_tokens)
```

Key properties:
- **Retry is on transient failures only** (timeouts, 5xx, 429). Auth errors
  and 4xx do not retry — they fall through to the fallback.
- **Fallback is explicit**, not a magic toggle. We control exactly when it
  fires and what counts as "primary failed."
- **No infinite loop** — `stop_after_attempt(2)` is hard. The n8n #18797 bug
  (Fallback Model + Retry On Fail looping forever) is structurally impossible
  here because retry and fallback are separate code paths, not nested
  behaviors of one node.
- The coach pass and the propose pass share the same `LLMClient`. The prompts
  differ; the client doesn't care.

## 6. Google Tasks client and the `invalid_grant` branch

```python
# src/leetcode_coach/integrations/google_tasks.py (sketch)
class GoogleTasksClient:
    async def mark_complete(self, task_id: str, notes_append: str) -> None:
        try:
            ...  # get + update call
        except RefreshError as e:
            if "invalid_grant" in str(e):
                raise GoogleAuthExpiredError() from e
            raise
```

`GoogleAuthExpiredError` is caught at the flow level and routed to a **distinct
Telegram alert** ("Google auth expired — re-authenticate the Google credential"),
per NFR-1 layer 2. It does **not** propagate to the global handler, and the
coach pass is **never** told to "log with estimated defaults" — that
anti-pattern from the old Discord workflow is explicitly forbidden.

**Ops note:** the root cause of repeated `invalid_grant` is the GCP OAuth
consent screen being in `Testing` status, which hard-expires refresh tokens
at 7 days. The fix is to flip the consent screen to `In production` (single-user
personal use, no verification needed). Documented in
`n8n-reference/README.md` lines 325-353 — that guidance carries over unchanged.

## 7. Database schema (SQLModel)

Direct mapping of the four tables in `business-requirements.md` §5. Migrations
via Alembic. Notable choices:

- `slug` is the PK of `leetcode_problems` (LeetCode slugs are stable).
- `pending_review` has a composite uniqueness expectation: at most 2 rows per
  day with `status = open`. Enforced in application code (Flow A caps picks
  at 2), not via a DB constraint — a constraint would make the expiry sweep
  awkward.
- `tutor_lessons.title` is not unique by DB constraint (the coach may surface
  near-duplicates); dedup is by similarity match in `flow_b.py` before insert.
- All timestamps are `DATE` (not `TIMESTRTZ`) — the system is single-timezone.

## 8. Configuration (env vars)

```dotenv
# .env.example
DATABASE_URL=postgresql+psycopg://user:pass@host:5432/leetcode_coach
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=              # the allowlist (single chat)
TELEGRAM_WEBHOOK_URL=          # public URL Telegram will POST to
OPENAI_API_KEY=
GEMINI_API_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
GOOGLE_TASKS_LIST_ID=
YOUTUBE_API_KEY=               # optional; if absent, YouTube search disabled
LEETCODE_USERNAME=
TIMEZONE=Europe/Bucharest
LOG_LEVEL=INFO
```

Loaded via `pydantic-settings`. Missing required vars fail fast at startup
with a clear error. No secrets in the repo, ever.

## 9. Deployment on Coolify

- **One container**, built from `Dockerfile` (multi-stage: uv install → slim
  runtime).
- **Postgres**: provisioned as a Coolify-managed Postgres service. The app
  reads `DATABASE_URL` from Coolify's env injection.
- **Webhook URL**: Coolify gives the container a public HTTPS URL; set
  `TELEGRAM_WEBHOOK_URL` to `https://<coolify-domain>/telegram/webhook` and
  the app calls `setWebhook` on startup.
- **Healthcheck**: `GET /health` returns 200 if the scheduler is running and
  the DB is reachable. Coolily watches this and restarts on failure.
- **Resource limits**: 256MB RAM is plenty. The app is idle 99% of the time.
- **Logs**: structured JSON to stdout. Coolify ships them.

## 10. Local development

```bash
uv sync
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn leetcode_coach.main:app --reload
# in another shell, for local Telegram testing:
uv run python -m leetcode_coach.scripts.ngrok_tunnel  # exposes :8000 + sets webhook
```

Tests use `respx` to mock HTTP and a `pytest-asyncio` fixture for the LLM
client with a fake `LLMClient` that returns canned responses. DB tests use
a throwaway Postgres via `testcontainers-postgres` — no mocking the DB.

## 11. Observability

- **Structured logs** (structlog): every flow run logs `flow=a|b|expiry`,
  `run_id`, `outcome`, `duration_ms`, `llm_model`, `tokens_in`, `tokens_out`.
- **Cost tracking**: each LLM call logs its token counts; a daily rollup
  logs `cost_usd` so the <$10/month NFR is verifiable from logs alone.
- **No metrics backend in v1** — logs are enough for a single-user system.
  If we later want a Grafana dashboard, structlog → Loki → Grafana is the
  path.

## 12. What this architecture deliberately does NOT do

- **No Celery / no Redis / no task queue.** APScheduler in-process is enough
  for 3 cron jobs and an on-demand webhook handler. Adding a queue is
  speculative complexity.
- **No separate worker process.** One container, one process (uvicorn +
  in-process scheduler). The system is single-user; horizontal scaling is
  not a concern.
- **No ORM magic.** SQLModel gives typed rows; queries are explicit
  SQLAlchemy `select()` calls. No `relationship=` cascades, no lazy loading
  surprises.
- **No LLM tool-calling loop in v1.** The n8n version gave the agent a
  YouTube search tool; in Python v1 we do the YouTube search (if enabled)
  **before** the LLM call and pass results in the prompt. Tool-calling loops
  add complexity and failure modes that aren't justified for a 1-search/day
  use case. Revisit if the coach pass needs to call tools mid-generation.
- **No Browserless / SearXNG in v1 unless a primary API actually fails.**
  Per the user's decision: "only if needed and as a fallback." The
  integration points are documented (§4) but the code paths are stubs.
