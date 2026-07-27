# #034 — Engineering principles & layer responsibilities

**Milestone:** M0 bootstrap · **Labels:** `type:infra` `prio:P0`
**Depends on:** none (applies from the first line of code)

## Summary
Define the layer boundaries, each layer's single clear responsibility, and the
programming principles every issue in this backlog must follow. This is the
guardrail doc: reviewers reject work that violates it. **The top principle is
KISS — Keep It Simple, Stupid.** Every other principle bends to it.

## Context
- The repo layout is already fixed in `docs/architecture.md` §3; this issue
  assigns a **responsibility contract** to each package so code lands in the
  right place with clean seams.
- `docs/architecture.md` §12 already forbids speculative complexity (no Celery,
  no queue, no ORM magic, no tool-calling loop). This issue makes that stance a
  first-class, enforceable convention.

## Principle hierarchy (highest wins on conflict)

1. **KISS — Keep It Simple, Stupid.** The simplest thing that satisfies the
   spec wins. If a pattern (SOLID or otherwise) adds indirection without a
   concrete, present need, drop it. Simplicity beats cleverness, every time.
2. **YAGNI — You Aren't Gonna Need It.** Build only what the current phase's
   exit criteria require. No "future-proofing" hooks. (Ties directly to
   architecture §12's out-of-scope list.)
3. **Separation of concerns / clear layer responsibility** (see contract
   below). One layer, one job.
4. **SOLID**, applied pragmatically (never at the expense of #1):
   - **S — Single Responsibility:** each module/function has one reason to
     change. A flow orchestrates; it does not talk HTTP directly.
   - **O — Open/Closed:** add a new LLM provider or integration by adding a
     client, not by editing flow logic.
   - **L — Liskov:** the fallback LLM client is substitutable for the primary
     behind the same `LLMClient.complete` contract (#010).
   - **I — Interface Segregation:** flows depend on the narrow client methods
     they use (`send_message`, `mark_complete`), not on fat god-objects.
   - **D — Dependency Inversion:** flows depend on client abstractions +
     typed models, injected in, so tests swap in mocks (#014, #018, #027).
5. **DRY**, but only for knowledge that is genuinely the same. Do not abstract
   two things that merely *look* alike (a common way to violate #1).
6. **Fail loud, fail typed.** Errors surface as typed exceptions and alerts
   (#008); never "log with estimated defaults" (NFR-1 layer 2).
7. **Explicit over implicit.** Explicit `select()` queries, explicit fallback,
   explicit env config — no hidden lazy-loading or magic (architecture §12).

## Layer responsibility contract (maps to architecture §3)

| Layer / package | Owns (single responsibility) | Must NOT do |
|---|---|---|
| `config.py` | Load + validate env into typed settings | Business logic; network calls |
| `db/models.py`, `db/base.py` | Table shapes; engine/session | HTTP; LLM; flow decisions |
| `integrations/*` | One external service each; retries, typed errors | Flow orchestration; DB writes of business state |
| `prompts/*` | Verbatim prompt text + output contract | Calling the LLM; parsing side effects |
| `flows/*` | Orchestration: sequence integrations + DB per FR | Raw HTTP; SQL string-building; prompt text |
| `scheduling/cron.py` | Register + fire jobs on a clock | Business logic (delegates to flows) |
| `webhooks/telegram.py` | Parse inbound update; allowlist; dispatch | Coach/pick logic (delegates to `flow_b`) |
| `errors.py` | Typed exception hierarchy + `send_alert` | Swallowing errors silently |

Rule of thumb: **integrations know *how* to talk to a service; flows know
*when* and *why*; prompts hold *what to say*; the DB layer holds *state*.**
A dependency arrow only ever points "inward" (flows → integrations/db →
config); it never points back out.

## Tasks
- [ ] Add a concise `CONTRIBUTING.md` (or a `## Engineering principles`
      section in `README.md`) capturing the principle hierarchy + the layer
      responsibility table above.
- [ ] Reference it from `AGENTS.md` so agents pick it up cold.
- [ ] Encode the mechanical parts in tooling where cheap: `ruff` rules for
      import hygiene; a lightweight import-layering check (flows must not import
      `httpx`/provider SDKs directly — they go through `integrations/*`).

## Acceptance criteria
- [ ] The principle hierarchy is documented with **KISS listed first** and an
      explicit "higher wins on conflict" rule.
- [ ] Every layer in architecture §3 has a one-line responsibility + an
      explicit "must not" in the checked-in doc.
- [ ] A CI/lint check (or documented review checklist item) flags a flow module
      importing an HTTP/provider SDK directly.
- [ ] Existing/upcoming issues can cite this doc as the definition of "clean"
      for their reviews.

## Notes
- This is a guardrail, not new runtime behavior — it must not add abstraction
  layers of its own (that would violate its own #1). Keep the doc short.
- When KISS and SOLID disagree, KISS wins and the reviewer records why in the
  PR.
