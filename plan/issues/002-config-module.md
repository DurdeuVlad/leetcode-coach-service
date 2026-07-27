# #002 — Config module (pydantic-settings, fail-fast)

**Milestone:** M0 bootstrap · **Labels:** `type:infra` `prio:P0`
**Depends on:** #001

## Summary
Typed, env-var-backed settings that fail fast at startup when a required secret
is missing.

## Context
- Env var list is fixed in `docs/architecture.md` §8.
- Security rule (NFR-4): secrets only in env vars, never in the repo.
- `.env.example` carries **keys only, never values**.

## Tasks
- [ ] `src/leetcode_coach/config.py` — `Settings(BaseSettings)` with every var
      from architecture §8:
      `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
      `TELEGRAM_WEBHOOK_URL`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
      `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`,
      `GOOGLE_TASKS_LIST_ID`, `YOUTUBE_API_KEY` (optional),
      `LEETCODE_USERNAME`, `TIMEZONE` (default `Europe/Bucharest`),
      `LOG_LEVEL` (default `INFO`).
- [ ] `YOUTUBE_API_KEY` is `Optional[str]` (unset ⇒ YouTube disabled, #013).
- [ ] Provide a single cached accessor (e.g. `get_settings()`).
- [ ] `.env.example` listing all keys with empty values.

## Acceptance criteria
- [ ] Missing a required var raises a clear `ValidationError` at import/startup,
      naming the missing field.
- [ ] `TIMEZONE` defaults to `Europe/Bucharest` when unset.
- [ ] Optional `YOUTUBE_API_KEY` absent ⇒ settings still load.
- [ ] Unit test loads settings from a monkeypatched env and asserts fail-fast.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** one `Settings` class + one cached `get_settings()`. No config
  framework, no layered override files.
- **Single Responsibility:** this module only loads + validates env. Zero
  network calls, zero business logic.
- **Explicit over implicit / fail loud:** missing required vars raise at
  startup naming the field — never a silent default that masks a misconfig.
- **Dependency Inversion:** all other layers read config through this module,
  so tests inject settings instead of touching the environment.

## Notes
- Do not read secrets anywhere except through this module.
