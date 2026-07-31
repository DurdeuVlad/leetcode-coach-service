"""Flow A — the daily 5-candidate proposal.

Single responsibility (per #034): orchestrate the gather → LLM → parse → send
pipeline. Holds no prompt text (that's `prompts/propose.py`), no raw HTTP
(that's the integration clients), no SQL strings (that's `db/`).

BUG-1 fix (docs/business-requirements.md FR-1.2): the n8n version only fetched
`solved = true` rows and passed them to the prompt; Flow A never saw the
unsolved pool. This implementation reads `leetcode_problems WHERE solved = false`
and passes THAT into the prompt. The regression test in #018 verifies the
unsolved pool reaches the prompt.

Candidate persistence is deliberately NOT here — per roadmap Phase 3a, storing
the 5-candidate array for Flow B to map picks is wired in #020. Phase 2 exit
needs only the message sent + the BUG-1 regression.
"""

from __future__ import annotations

import datetime
import html
import json
from collections.abc import Sequence

import structlog
from sqlmodel import select

from leetcode_coach.config import get_settings
from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import DailyCandidate, LeetCodeLog, LeetCodeProblem, TutorLesson
from leetcode_coach.errors import LeetCodeCoachError
from leetcode_coach.integrations.llm import LLMClient
from leetcode_coach.integrations.telegram import send_message
from leetcode_coach.prompts.propose import PROPOSE_PROMPT, PROPOSE_SYSTEM

log = structlog.get_logger("flow_a")

_REQUIRED_CANDIDATE_KEYS = (
    "slug",
    "title",
    "url",
    "tags",
    "difficulty",
    "reasoning",
    "coaching_hint",
)
_VALID_DIFFICULTIES = ("easy", "medium", "hard")

# Difficulty → emoji badge for the propose card (docs/telegram-formatting.md
# §3.2.1). Mapped in code, NOT in the prompt — the LLM emits the lowercase
# difficulty string and the code renders the badge.
_DIFFICULTY_BADGE = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}


class ProposeValidationError(LeetCodeCoachError):
    """The LLM returned structurally invalid candidates (bad mix, missing
    keys, wrong count). Fail loud (NFR-1 layer 2) rather than ship bad data."""


def _gather_data() -> tuple[list[dict], list[dict], list[dict]]:
    """Read the three inputs the prompt needs.

    Returns (recent_log_rows, unsolved_problems, active_lessons) as plain
    dicts (SQLModel rows are serialized via `.model_dump()`).

    BUG-1: reads `solved = false`, NOT `solved = true`.
    """
    with next(get_session()) as session:
        recent_log = list(
            session.exec(select(LeetCodeLog).order_by(LeetCodeLog.id.desc()).limit(30)).all()
        )
        unsolved = list(
            session.exec(
                select(LeetCodeProblem).where(LeetCodeProblem.solved == False)  # noqa: E712
            ).all()
        )
        active_lessons = list(
            session.exec(
                select(TutorLesson).where(TutorLesson.active == True)  # noqa: E712
            ).all()
        )
    return (
        [r.model_dump(mode="json") for r in recent_log],
        [p.model_dump(mode="json") for p in unsolved],
        [les.model_dump(mode="json") for les in active_lessons],
    )


def _build_prompt(
    recent_log: Sequence[dict],
    unsolved_pool: Sequence[dict],
    active_lessons: Sequence[dict],
) -> str:
    """Fill the propose prompt template with the gathered data.

    Exposed as a module-level function so the BUG-1 regression test (#018)
    can assert the unsolved pool is actually present in the rendered prompt.
    """
    return PROPOSE_PROMPT.format(
        today=datetime.date.today().isoformat(),
        recent_log_json=json.dumps(list(recent_log), default=str, indent=2),
        unsolved_pool_json=json.dumps(list(unsolved_pool), default=str, indent=2),
        active_lessons_json=json.dumps(list(active_lessons), default=str, indent=2),
    )


def _parse_candidates(raw_text: str) -> list[dict]:
    """Parse the LLM JSON response into the candidates list.

    Strips a single pair of markdown code fences if present (some models wrap
    JSON in ```json ... ``` despite the prompt saying not to). Raises
    `ProposeValidationError` on any structural problem.

    The LLM no longer emits a `candidate_list_markdown` field
    (docs/telegram-formatting.md §3.2.1) — the code renders the HTML card
    from the `candidates` array via `_render_propose_html`.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        # Strip opening fence (```json or ```) and closing ```.
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProposeValidationError(f"LLM response was not valid JSON: {e}") from e

    if not isinstance(payload, dict):
        raise ProposeValidationError("LLM response top-level was not a JSON object")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ProposeValidationError("candidates was not a JSON array")
    return candidates


def _render_propose_html(candidates: Sequence[dict]) -> str:
    """Render the 5-candidate propose card as Telegram HTML.

    The LLM emits plain-text fields; this function wraps them in a fixed
    HTML template with `html.escape` on every LLM-derived string. Sent with
    `parse_mode="HTML"`. See docs/telegram-formatting.md §3.2.1 for the
    target shape and the rationale (MarkdownV2 was unreliable; HTML + escape
    is strictly simpler).

    All LLM-derived strings (`title`, `tags`, `reasoning`, `coaching_hint`)
    pass through `html.escape`. URLs come from the validated
    `leetcode.com/problems/<slug>/` shape (enforced by
    `_validate_candidates`), so they're safe to embed; `html.escape` on the
    URL too is defensive and harmless.
    """
    lines: list[str] = ["<b>📊 Today's Problems</b>", ""]
    for i, c in enumerate(candidates, start=1):
        title = html.escape(str(c["title"]))
        url = html.escape(str(c["url"]), quote=True)
        tags = html.escape(str(c["tags"]))
        difficulty = str(c["difficulty"]).lower()
        badge = _DIFFICULTY_BADGE.get(difficulty, "")
        reasoning = html.escape(str(c.get("reasoning", "")))
        hint = html.escape(str(c.get("coaching_hint", "")))
        lines.append(
            f'<b>{i}. <a href="{url}">{title}</a></b> {badge} {html.escape(difficulty)}'
        )
        lines.append(f"<i>{tags}</i>")
        lines.append(
            f"<blockquote><b>Why:</b> {reasoning}\n<b>Hint:</b> {hint}</blockquote>"
        )
        lines.append("")
    return "\n".join(lines).rstrip()


def _validate_candidates(candidates: Sequence[dict]) -> None:
    """Defensive validation of the LLM output (FR-1.2, FR-1.3, FR-1.7).

    Fail loud: reject bad LLM output rather than silently shipping it.
    """
    if len(candidates) != 5:
        raise ProposeValidationError(f"expected exactly 5 candidates, got {len(candidates)}")
    difficulties: list[str] = []
    for i, c in enumerate(candidates):
        if not isinstance(c, dict):
            raise ProposeValidationError(f"candidate {i} is not an object")
        missing = [k for k in _REQUIRED_CANDIDATE_KEYS if k not in c]
        if missing:
            raise ProposeValidationError(f"candidate {i} missing keys: {missing}")
        diff = str(c["difficulty"]).lower()
        if diff not in _VALID_DIFFICULTIES:
            raise ProposeValidationError(
                f"candidate {i} has invalid difficulty '{c['difficulty']}'"
            )
        difficulties.append(diff)
        # FR-1.7: URLs must match the leetcode.com/problems/<slug>/ shape.
        url = str(c["url"])
        if "leetcode.com/problems/" not in url:
            raise ProposeValidationError(
                f"candidate {i} URL does not look like a leetcode problem URL: {url}"
            )

    # FR-1.3: 2-3 hard + 2-3 medium. Never 5 of one difficulty.
    hard = difficulties.count("hard")
    medium = difficulties.count("medium")
    easy = difficulties.count("easy")
    if easy > 1:
        # The spec says 2-3 hard + 2-3 medium; easy is allowed only as the
        # single spaced-repetition solved pick. >1 easy violates the mix.
        raise ProposeValidationError(f"difficulty mix violated: {easy} easy (max 1 allowed)")
    if hard < 2 or hard > 3 or medium < 2 or medium > 3:
        raise ProposeValidationError(
            f"difficulty mix violated: {hard} hard, {medium} medium "
            f"(need 2-3 hard + 2-3 medium)"
        )


def _persist_candidates(candidates: Sequence[dict]) -> None:
    """Persist the 5 candidates into `daily_candidates` for Flow B's pick-parse.

    Issue #020 decision: a dedicated `daily_candidates` table (keyed by
    `proposed_at` + `pick_index`) — NOT pre-inserted `pending_review` rows.
    Preserves the FR-2.2 routing invariant (the 5-list message_id is never
    in `pending_review`).

    Idempotent: deletes today's existing rows first, then inserts 5. This
    makes a re-run of Flow A (manual or via a missed-cron catch-up) replace
    rather than duplicate. YAGNI: no historical archive.
    """
    today = datetime.date.today()
    with next(get_session()) as session:
        # Idempotent re-run: clear today's rows before inserting.
        existing = session.exec(
            select(DailyCandidate).where(DailyCandidate.proposed_at == today)
        ).all()
        for row in existing:
            session.delete(row)
        for idx, c in enumerate(candidates, start=1):
            session.add(
                DailyCandidate(
                    proposed_at=today,
                    pick_index=idx,
                    slug=str(c["slug"]),
                    title=str(c["title"]),
                    url=str(c["url"]),
                    tags=str(c["tags"]),
                    difficulty=str(c["difficulty"]),
                    reasoning=str(c.get("reasoning", "")),
                    coaching_hint=str(c.get("coaching_hint", "")),
                )
            )
        session.commit()
    log.info("flow_a_persisted_candidates", count=len(candidates), date=today.isoformat())


async def propose_5(
    *,
    llm: LLMClient | None = None,
    chat_id: str | None = None,
    dry_run: bool = False,
) -> str:
    """Run the daily proposal: gather data, call LLM, parse, send to Telegram,
    then persist the 5 candidates for Flow B's pick-parse path (#020).

    Returns the HTML card that was sent (useful for tests and for #020 to
    persist alongside the candidates).

    Args:
        llm: injected LLMClient (tests pass a mock; production uses the default).
        chat_id: override the target chat (tests use this; production uses the
            configured TELEGRAM_CHAT_ID via send_message's allowlist default).
        dry_run: skip the Telegram send (admin/test path). Candidates are still
            persisted so Flow B's pick-parse can run. The HTML is returned
            either way.

    Flow (FR-1.6): after sending the single numbered message, the flow ENDS.
    It does not wait for the reply — replies are Flow B (#019+).
    """
    settings = get_settings()
    target_chat = chat_id or settings.telegram_chat_id
    client = llm or LLMClient()

    recent_log, unsolved_pool, active_lessons = _gather_data()
    log.info(
        "flow_a_gathered",
        recent_log_count=len(recent_log),
        unsolved_pool_count=len(unsolved_pool),
        active_lessons_count=len(active_lessons),
    )

    user_prompt = _build_prompt(recent_log, unsolved_pool, active_lessons)
    response = await client.complete(PROPOSE_SYSTEM, user_prompt)
    candidates = _parse_candidates(response.text)
    _validate_candidates(candidates)
    html_card = _render_propose_html(candidates)

    if not dry_run:
        # HTML send: the code renders the card from the candidates array
        # (docs/telegram-formatting.md §3.2.1). All LLM-derived strings are
        # html.escape'd in _render_propose_html. The previous plain-text send
        # leaked MarkdownV2 escape artifacts (`\.`, `\-`) into the user-facing
        # message because the LLM was told to emit MarkdownV2 but parse_mode
        # was omitted (MarkdownV2 rejects on missing escapes).
        await send_message(target_chat, html_card, parse_mode="HTML")
    # Persist the 5 candidates so Flow B's pick-parse can map reply numbers
    # → problems (issue #020). Done AFTER the send so a send failure doesn't
    # leave stale candidates; a persist failure here is loud (NFR-1 layer 2).
    _persist_candidates(candidates)
    log.info(
        "flow_a_sent",
        chars=len(html_card),
        model=response.model,
        tokens_in=response.tokens_in,
        tokens_out=response.tokens_out,
        dry_run=dry_run,
    )

    # FR-8.2: refresh the pinned progression message after Flow A runs.
    # Fire-and-forget: a pinned-message failure must not fail the flow.
    if not dry_run:
        try:
            from leetcode_coach.flows.pinned import refresh_pinned_message

            await refresh_pinned_message()
        except Exception:
            log.warning("pinned_refresh_failed_after_propose")

    return html_card
