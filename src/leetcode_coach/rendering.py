"""Deterministic Telegram renderers. Models never generate markup."""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import Any

_BADGES = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}


def _field(item: object, name: str, default: Any = "") -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _plain(value: object) -> str:
    """Remove legacy MarkdownV2 escapes before applying HTML escaping."""
    return str(value).replace(r"\.", ".").replace(r"\-", "-")


def _clip(value: object, limit: int) -> str:
    text = _plain(value)
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def render_proposal_html(candidates: Sequence[object], *, balance: str | None = None) -> str:
    lines = ["<b>📊 Today's Problems</b>", ""]
    for index, candidate in enumerate(candidates, 1):
        title = html.escape(_clip(_field(candidate, "title"), 120))
        url = html.escape(str(_field(candidate, "url")), quote=True)
        difficulty = str(_field(candidate, "difficulty")).lower()
        tags = html.escape(_clip(_field(candidate, "tags"), 120))
        reasoning = html.escape(_clip(_field(candidate, "reasoning"), 220))
        hint = html.escape(_clip(_field(candidate, "coaching_hint"), 220))
        lines.extend(
            [
                f'<b>{index}. <a href="{url}">{title}</a></b> '
                f'{_BADGES.get(difficulty, "")} {html.escape(difficulty)}',
                f"<i>{tags}</i>",
                f"<blockquote><b>Why:</b> {reasoning}\n<b>Hint:</b> {hint}</blockquote>",
                "",
            ]
        )
    if balance is not None:
        lines.append(f"<b>Credits: {html.escape(_clip(balance, 100))}</b>")
    return "\n".join(lines).rstrip()


def approval_keyboard(approval_id: str) -> dict[str, list[list[dict[str, str]]]]:
    return {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": f"v2a:{approval_id}:yes"},
                {"text": "Reject", "callback_data": f"v2a:{approval_id}:no"},
            ]
        ]
    }


def proposal_keyboard(batch_id: int, count: int) -> dict[str, list[list[dict[str, str]]]]:
    return {
        "inline_keyboard": [
            [
                {"text": str(index), "callback_data": f"v2p:{batch_id}:{index}"}
                for index in range(1, count + 1)
            ]
        ]
    }
