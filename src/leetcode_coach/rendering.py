"""Deterministic Telegram renderers. Models never generate markup."""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
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


class _VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.length = 0

    def handle_data(self, data: str) -> None:
        self.length += len(data)


def _visible_length(value: str) -> int:
    parser = _VisibleHTML()
    parser.feed(value)
    parser.close()
    return parser.length


def render_proposal_html(
    candidates: Sequence[object], *, balance: str | None = None, start_position: int = 1
) -> str:
    lines = ["<b>📊 Today's Problems</b>", ""]
    for index, candidate in enumerate(candidates, start_position):
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


def paginate_proposal_html(
    candidates: Sequence[object], *, max_visible: int = 4096
) -> list[tuple[str, list[int]]]:
    """Render every candidate into deterministic Telegram-safe HTML pages."""
    if max_visible < 1:
        raise ValueError("max_visible must be positive")
    pages: list[tuple[str, list[int]]] = []
    current: list[object] = []
    start_position = 1
    for candidate in candidates:
        trial = [*current, candidate]
        rendered = render_proposal_html(trial, start_position=start_position)
        if current and _visible_length(rendered) > max_visible:
            text = render_proposal_html(current, start_position=start_position)
            positions = list(range(start_position, start_position + len(current)))
            pages.append((text, positions))
            start_position += len(current)
            current = [candidate]
        else:
            current = trial
    if current:
        text = render_proposal_html(current, start_position=start_position)
        if _visible_length(text) > max_visible:
            raise ValueError("one proposal card exceeds the Telegram visible-text limit")
        pages.append((text, list(range(start_position, start_position + len(current)))))
    return pages


def render_work_receipt(receipt: Mapping[str, object]) -> str:
    """Render authoritative attempt facts as plain Telegram text."""

    heading = "Already recorded" if receipt.get("replayed") is True else "Your work counts"
    return "\n".join(
        [
            heading,
            "",
            f"Problem: {_plain(receipt['title'])}",
            f"Result: {_plain(receipt['result'])}",
            f"Credit: {_plain(receipt['credit'])}",
            f"Balance: {_plain(receipt['balance'])}",
            f"Path: {_plain(receipt['path'])}",
        ]
    )


def proposal_keyboard(
    batch_id: int, positions: int | Sequence[int]
) -> dict[str, list[list[dict[str, str]]]]:
    available = list(range(1, positions + 1)) if isinstance(positions, int) else list(positions)
    rows = [
        [
            {"text": f"Pick {index}", "callback_data": f"v2p:{batch_id}:{index}"}
            for index in available[offset : offset + 4]
        ]
        for offset in range(0, len(available), 4)
    ]
    rows.append([{"text": "Done", "callback_data": f"v2pd:{batch_id}"}])
    return {"inline_keyboard": rows}
