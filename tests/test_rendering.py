from leetcode_coach.integrations.telegram import _message_text
from leetcode_coach.rendering import (
    paginate_proposal_html,
    render_proposal_html,
)


def test_renderer_escapes_model_text_and_never_emits_markdown_escapes():
    text = render_proposal_html(
        [
            {
                "title": "A < B & C",
                "url": "https://leetcode.com/problems/a-b/",
                "difficulty": "easy",
                "tags": "array & math",
                "reasoning": r"No raw \. or \- escapes",
                "coaching_hint": "x < y",
            }
        ]
    )
    assert "A &lt; B &amp; C" in text
    assert "x &lt; y" in text
    assert r"\." not in text
    assert r"\-" not in text


def test_proposal_renderer_bounds_model_fields_without_breaking_html():
    text = render_proposal_html(
        [
            {
                "title": "<&" * 500,
                "url": "https://leetcode.com/problems/example/",
                "difficulty": "medium",
                "tags": "<&" * 500,
                "reasoning": "<&" * 500,
                "coaching_hint": "<&" * 500,
            }
            for _ in range(5)
        ]
    )

    assert "…" in text
    assert text.count("<blockquote>") == 5
    assert text.count("</blockquote>") == 5


def test_large_flexible_proposal_is_paginated_to_telegram_safe_html():
    cards = [
        {
            "title": f"Problem {index} " + "T" * 200,
            "url": f"https://leetcode.com/problems/problem-{index}/",
            "difficulty": "hard",
            "tags": "tag" * 100,
            "reasoning": "R" * 500,
            "coaching_hint": "H" * 500,
        }
        for index in range(1, 13)
    ]

    pages = paginate_proposal_html(cards)

    assert len(pages) > 1
    assert [position for _, positions in pages for position in positions] == list(range(1, 13))
    assert all(_message_text(text, "HTML") == text for text, _ in pages)
