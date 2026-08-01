"""Terminal-based Telegram simulator for local prompt engineering & testing.

Boots the real Flow A / Flow B / slash-command logic against the configured
Postgres, monkeypatches the Telegram outbound functions to print to stdout
(and return synthetic monotonic message_ids so Flow B's reply-to correlation
works exactly like real Telegram), and reads lines from stdin as if they were
inbound Telegram messages.

Run:
    python -m leetcode_coach.scripts.terminal

Env (reuse existing vars, no new ones):
    DATABASE_URL=postgresql+psycopg://leetcode:leetcode@localhost:5432/leetcode_coach
    TELEGRAM_BOT_TOKEN=mock
    TELEGRAM_CHAT_ID=123456
    OPENAI_API_KEY=<real key for prompt testing>  # or "mock" for canned JSON
    GEMINI_API_KEY=mock

See :help inside the REPL for the full command list.
"""

from __future__ import annotations

import asyncio
import datetime
import html as html_lib
import re
import sys

import structlog
from sqlmodel import select
from telegram import Update

from leetcode_coach.config import get_settings
from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import LeetCodeProblem, PendingReview
from leetcode_coach.flows import flow_a, flow_b
from leetcode_coach.flows import commands as commands_mod
from leetcode_coach.flows import expiry as expiry_mod
from leetcode_coach.flows import pinned as pinned_mod
from leetcode_coach.integrations import llm as llm_mod
from leetcode_coach.integrations import telegram as tg_mod
from leetcode_coach.integrations.llm import LLMClient
from leetcode_coach.prompts.coach import COACH_SYSTEM
from leetcode_coach.prompts.propose import PROPOSE_SYSTEM
from leetcode_coach.scripts._seed import reset_and_seed, seed_if_empty

log = structlog.get_logger("terminal")

# --- Outbound Telegram simulation state -------------------------------------
#
# Synthetic message_ids returned by the patched send_* functions. Outbound
# ids start at 10000 so they never collide with inbound ids (1..). The
# mapping lets :threads and reply-to debugging show what each id was.

_next_outbound_id = 10000
_outbound_messages: dict[int, str] = {}

# Inbound message_id counter (the user's typed messages).
_next_inbound_id = 1

# Set by :reply <id>; consumed by the next non-meta line, then reset.
_reply_target: int | None = None

# :raw toggle — when True, wraps LLMClient.complete to print the rendered
# prompt + raw response on every flow call.
_raw_llm = False
_original_complete = LLMClient.complete


def _strip_html(text: str) -> str:
    """Strip Telegram HTML tags + unescape entities for terminal display."""
    no_tags = re.sub(r"<[^>]+>", "", text)
    return html_lib.unescape(no_tags)


def _print_outbound(label: str, text: str, *, reply_to: int | None = None) -> None:
    """Print a simulated outbound Telegram message with a synthetic id."""
    global _next_outbound_id
    msg_id = _next_outbound_id
    _next_outbound_id += 1
    _outbound_messages[msg_id] = text
    prefix = f"\n[{label} #{msg_id}]"
    if reply_to is not None:
        prefix += f" (reply to #{reply_to})"
    print(f"{prefix}\n{_strip_html(text)}\n", flush=True)
    return msg_id


# --- Monkeypatched outbound functions ---------------------------------------


async def _term_send_message(
    chat_id: str,
    text: str,
    *,
    reply_markup: dict | None = None,
    parse_mode: str | None = None,
) -> int:
    return _print_outbound("BOT", text)


async def _term_send_reply(
    chat_id: str,
    reply_to_message_id: int,
    text: str,
    *,
    reply_markup: dict | None = None,
    parse_mode: str | None = None,
) -> int:
    return _print_outbound("BOT", text, reply_to=reply_to_message_id)


async def _term_edit_message_text(
    chat_id: str, message_id: int, text: str, *, parse_mode: str | None = None
) -> dict:
    _outbound_messages[message_id] = text
    print(f"\n[BOT edit #{message_id}]\n{_strip_html(text)}\n", flush=True)
    return {"ok": True}


async def _term_pin_message(chat_id: str, message_id: int) -> None:
    print(f"[BOT pin #{message_id}]", flush=True)


async def _term_unpin_message(chat_id: str, message_id: int) -> None:
    print(f"[BOT unpin #{message_id}]", flush=True)


def _patch_telegram_outbound() -> None:
    """Replace every module's reference to the Telegram send functions.

    Modules that did ``from leetcode_coach.integrations.telegram import
    send_message`` bound the name at import time, so patching the source
    module alone is not enough — each importer's bound reference must be
    replaced too. Same pattern the test suite uses (monkeypatch.setattr on
    each module).
    """
    tg_mod.send_message = _term_send_message  # type: ignore[assignment]
    tg_mod.send_reply = _term_send_reply  # type: ignore[assignment]
    tg_mod.edit_message_text = _term_edit_message_text  # type: ignore[assignment]
    tg_mod.pin_message = _term_pin_message  # type: ignore[assignment]
    tg_mod.unpin_message = _term_unpin_message  # type: ignore[assignment]

    flow_a.send_message = _term_send_message  # type: ignore[assignment]
    flow_b.send_message = _term_send_message  # type: ignore[assignment]
    flow_b.send_reply = _term_send_reply  # type: ignore[assignment]
    commands_mod.send_message = _term_send_message  # type: ignore[assignment]
    expiry_mod.send_message = _term_send_message  # type: ignore[assignment]
    pinned_mod.send_message = _term_send_message  # type: ignore[assignment]
    pinned_mod.send_reply = _term_send_reply  # type: ignore[assignment]
    pinned_mod.edit_message_text = _term_edit_message_text  # type: ignore[assignment]
    pinned_mod.pin_message = _term_pin_message  # type: ignore[assignment]
    pinned_mod.unpin_message = _term_unpin_message  # type: ignore[assignment]


# --- :raw LLM wrapper -------------------------------------------------------


async def _raw_complete(self: LLMClient, system: str, user: str, *, max_tokens: int = 2000):
    """Wrapper around LLMClient.complete that prints the prompt + response."""
    print("\n[prompt] ------ system ------", flush=True)
    print(system, flush=True)
    print("[prompt] ------ user ------", flush=True)
    print(user, flush=True)
    print("[prompt] ------ end ------\n", flush=True)
    resp = await _original_complete(self, system, user, max_tokens=max_tokens)
    print("\n[llm] ------ response ------", flush=True)
    print(resp.text, flush=True)
    print(
        f"[llm] model={resp.model} tokens_in={resp.tokens_in} tokens_out={resp.tokens_out}\n",
        flush=True,
    )
    return resp


def _set_raw_llm(enabled: bool) -> None:
    global _raw_llm
    if enabled and not _raw_llm:
        LLMClient.complete = _raw_complete  # type: ignore[assignment]
        _raw_llm = True
    elif not enabled and _raw_llm:
        LLMClient.complete = _original_complete  # type: ignore[assignment]
        _raw_llm = False


# --- Update construction ----------------------------------------------------


def _make_update(
    *,
    text: str,
    chat_id: int,
    message_id: int,
    reply_to_message_id: int | None = None,
) -> Update:
    """Build a telegram.Update the way python-telegram-bot's de_json does.

    Same construction pattern as tests/test_flow_b.py::_make_update — we
    build the raw dict and call Update.de_json so typed fields populate the
    same way the webhook route does it.
    """
    msg: dict = {
        "message_id": message_id,
        "date": 1700000000,
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "is_bot": False, "first_name": "you"},
        "text": text,
    }
    if reply_to_message_id is not None:
        # The replied-to text is cosmetic; only message_id is used by routing.
        prior = _outbound_messages.get(reply_to_message_id, "(earlier message)")
        msg["reply_to_message"] = {
            "message_id": reply_to_message_id,
            "date": 1700000000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "bot"},
            "text": prior,
        }
    data = {"update_id": message_id, "message": msg}
    return Update.de_json(data, bot=None)


# --- Meta-commands ----------------------------------------------------------


def _resolve_coach_target(slug: str | None) -> PendingReview | None:
    """Resolve an open pending_review for today for :prompt/:llm coach.

    Mirrors the /coach waterfall (cheapest first, no LLM): if a slug is
    given, match it; else if exactly one open review today, use it; else
    print the list (or 'none') and return None.
    """
    today = datetime.date.today()
    with next(get_session()) as session:
        open_reviews = session.exec(
            select(PendingReview).where(
                PendingReview.proposed_at == today,
                PendingReview.status == "open",
            )
        ).all()
    if slug:
        match = next((r for r in open_reviews if r.problem_slug == slug), None)
        if match is None:
            print(f"No open review for slug '{slug}' today.", flush=True)
        return match
    if not open_reviews:
        print("No open problems today. Run /propose then /pick first.", flush=True)
        return None
    if len(open_reviews) == 1:
        return open_reviews[0]
    print("Multiple open problems today — specify a slug:", flush=True)
    for r in open_reviews:
        print(f"  {r.problem_slug}  ({r.problem_title})", flush=True)
    return None


async def _cmd_prompt_propose() -> None:
    recent_log, unsolved, active_lessons = flow_a._gather_data()
    user_prompt = flow_a._build_prompt(recent_log, unsolved, active_lessons)
    print("\n[prompt] ------ system ------", flush=True)
    print(PROPOSE_SYSTEM, flush=True)
    print("[prompt] ------ user ------", flush=True)
    print(user_prompt, flush=True)
    print("[prompt] ------ end ------\n", flush=True)


async def _cmd_prompt_coach(slug: str | None) -> None:
    review = _resolve_coach_target(slug)
    if review is None:
        return
    with next(get_session()) as session:
        user_prompt, _ = flow_b._gather_coach_inputs(session, review, "")
    print("\n[prompt] ------ system ------", flush=True)
    print(COACH_SYSTEM, flush=True)
    print("[prompt] ------ user ------", flush=True)
    print(user_prompt, flush=True)
    print("[prompt] ------ end ------\n", flush=True)


async def _cmd_llm_propose() -> None:
    recent_log, unsolved, active_lessons = flow_a._gather_data()
    user_prompt = flow_a._build_prompt(recent_log, unsolved, active_lessons)
    print("\n[llm] calling LLM...\n", flush=True)
    resp = await LLMClient().complete(PROPOSE_SYSTEM, user_prompt)
    print("[llm] ------ response ------", flush=True)
    print(resp.text, flush=True)
    print(
        f"[llm] model={resp.model} tokens_in={resp.tokens_in} tokens_out={resp.tokens_out}\n",
        flush=True,
    )


async def _cmd_llm_coach(slug: str | None) -> None:
    review = _resolve_coach_target(slug)
    if review is None:
        return
    with next(get_session()) as session:
        user_prompt, _ = flow_b._gather_coach_inputs(session, review, "")
    print("\n[llm] calling LLM...\n", flush=True)
    resp = await LLMClient().complete(COACH_SYSTEM, user_prompt)
    print("[llm] ------ response ------", flush=True)
    print(resp.text, flush=True)
    print(
        f"[llm] model={resp.model} tokens_in={resp.tokens_in} tokens_out={resp.tokens_out}\n",
        flush=True,
    )


def _cmd_threads() -> None:
    today = datetime.date.today()
    with next(get_session()) as session:
        rows = session.exec(
            select(PendingReview).where(PendingReview.proposed_at == today)
        ).all()
    if not rows:
        print("No problem threads today. Run /propose then /pick.", flush=True)
        return
    print(f"\nThreads today ({len(rows)}):", flush=True)
    for r in rows:
        print(
            f"  #{r.message_id}  [{r.status}]  {r.problem_slug}  ({r.problem_title})",
            flush=True,
        )
    print("Use :reply <id> then paste your code to submit to a thread.\n", flush=True)


def _cmd_seed() -> None:
    with next(get_session()) as session:
        seeded = seed_if_empty(session)
    print("Seeded fixture." if seeded else "Already populated — no-op.", flush=True)


def _cmd_reset() -> None:
    with next(get_session()) as session:
        reset_and_seed(session)
    print("Wiped + re-seeded fixture.", flush=True)


async def _cmd_ping() -> None:
    """Probe every external service concurrently and print a status table."""
    from leetcode_coach.integrations.connectivity import ping_all, render_probe_table

    print("Pinging external services…", flush=True)
    results = await ping_all()
    print(render_probe_table(results), flush=True)


def _print_help() -> None:
    print(
        """
Terminal LeetCode Coach — commands:

Slash commands (passthrough to the real flow logic):
  /propose            run Flow A (5-candidate proposal) end-to-end
  /pick <ints>        e.g. /pick 1 2  — pick ≤2 problems from today's 5-list
  /coach <text>       coach pass; target via :reply or /coach <slug> <code>
  /status             read-only DB dump (no LLM)
  /why <slug>         one bounded LLM call explaining why that problem
  /help               command list

Meta-commands (terminal-only, do NOT go through the flows):
  :prompt propose     print the rendered Flow A prompt (system + user), no LLM
  :prompt coach [slug] print the rendered coach prompt for an open thread
  :llm propose        run the raw Flow A LLM call, print response + token counts
  :llm coach [slug]   run the raw coach LLM call, print response + token counts
  :raw                toggle printing rendered prompt + raw LLM response on
                      every flow call (off by default)
  :reply <id>         next non-meta line is sent as a reply to message <id>
                      (use :threads to list ids; mirrors replying in Telegram)
  :threads            list today's pending_review threads with their msg ids
  :seed               seed fixture problems+lessons if DB is empty (idempotent)
  :reset              wipe transient tables + re-seed fixture
  :ping               probe every external service (Telegram, OpenAI, Gemini,
                      Google Tasks, Browserless, SearXNG) and print status
  :help               this message

Paste mode: start a line with ``` to enter multi-line paste (end with ```).
Use it to paste code blocks for /coach submissions.

Ctrl+C / Ctrl+D to exit.
""",
        flush=True,
    )


# --- REPL -------------------------------------------------------------------


async def _read_line_async(prompt: str) -> str:
    """Read a line from stdin without blocking the event loop."""
    return await asyncio.to_thread(input, prompt)


async def _handle_meta(line: str) -> bool:
    """Handle a : meta-command. Returns True if handled (line consumed)."""
    parts = line.split(maxsplit=2)
    cmd = parts[0]
    arg1 = parts[1] if len(parts) > 1 else ""
    arg2 = parts[2] if len(parts) > 2 else ""

    if cmd == ":prompt":
        if arg1 == "propose":
            await _cmd_prompt_propose()
        elif arg1 == "coach":
            await _cmd_prompt_coach(arg2 or None)
        else:
            print("Usage: :prompt propose | :prompt coach [slug]", flush=True)
    elif cmd == ":llm":
        if arg1 == "propose":
            await _cmd_llm_propose()
        elif arg1 == "coach":
            await _cmd_llm_coach(arg2 or None)
        else:
            print("Usage: :llm propose | :llm coach [slug]", flush=True)
    elif cmd == ":raw":
        _set_raw_llm(not _raw_llm)
        print(f":raw {'ON' if _raw_llm else 'OFF'}", flush=True)
    elif cmd == ":reply":
        global _reply_target
        try:
            _reply_target = int(arg1)
            print(f"Next message will reply to #{_reply_target}.", flush=True)
        except ValueError:
            print("Usage: :reply <message_id>", flush=True)
    elif cmd == ":threads":
        _cmd_threads()
    elif cmd == ":seed":
        _cmd_seed()
    elif cmd == ":reset":
        _cmd_reset()
    elif cmd == ":ping":
        await _cmd_ping()
    elif cmd == ":help":
        _print_help()
    else:
        print(f"Unknown meta-command: {cmd}. Try :help.", flush=True)
    return True


async def _handle_line(line: str, chat_id: int) -> None:
    """Dispatch one non-empty input line: meta, paste, or Telegram update."""
    global _next_inbound_id, _reply_target

    if line.startswith(":"):
        await _handle_meta(line)
        return

    # Multi-line paste mode: ``` ... ```
    if line.strip() == "```":
        print("[paste mode] end with ``` to submit", flush=True)
        body_lines: list[str] = []
        while True:
            nl = await _read_line_async("... ")
            if nl.strip() == "```":
                break
            body_lines.append(nl)
        line = "\n".join(body_lines)
        if not line.strip():
            return

    msg_id = _next_inbound_id
    _next_inbound_id += 1
    reply_to = _reply_target
    _reply_target = None

    update = _make_update(
        text=line,
        chat_id=chat_id,
        message_id=msg_id,
        reply_to_message_id=reply_to,
    )
    print(f"\n[YOU #{msg_id}]" + (f" (reply to #{reply_to})" if reply_to else ""), flush=True)
    try:
        await flow_b.handle_update(update)
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", flush=True)


async def _preflight() -> int:
    """Verify DB reachable + auto-seed if empty. Returns the chat_id to use."""
    settings = get_settings()
    chat_id = int(settings.telegram_chat_id)

    try:
        with next(get_session()) as session:
            seeded = seed_if_empty(session)
    except Exception as e:
        print(f"[FATAL] cannot reach DATABASE_URL: {e}", flush=True)
        print(
            "Start Postgres first:  docker compose up -d postgres  &&  alembic upgrade head",
            flush=True,
        )
        sys.exit(1)

    llm_mode = "mock (canned JSON)" if llm_mod._is_mock() else "REAL OpenAI (paid calls)"
    print(
        f"\nTerminal LeetCode Coach ready.\n"
        f"  chat_id={chat_id}\n"
        f"  LLM mode: {llm_mode}\n"
        f"  DB seeded this session: {seeded}\n"
        f"  :help for commands.\n",
        flush=True,
    )
    return chat_id


async def main() -> None:
    # Windows console defaults to cp1252; the propose card uses emoji
    # (📊 🟢 🟡 🔴) and the help text uses ≤. Force UTF-8 so prints don't
    # crash with UnicodeEncodeError. `errors="replace"` is a last-resort
    # guard for terminals that still can't render a glyph.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass

    _patch_telegram_outbound()
    chat_id = await _preflight()
    _print_help()

    while True:
        try:
            line = await _read_line_async("> ")
        except (EOFError, KeyboardInterrupt):
            print("\nbye.", flush=True)
            break
        if not line.strip():
            continue
        await _handle_line(line, chat_id)


if __name__ == "__main__":
    asyncio.run(main())
