from leetcode_coach_v2.rendering import approval_keyboard, render_proposal_html


def test_renderer_escapes_model_text_and_never_emits_markdown_escapes():
    text = render_proposal_html(
        [{
            "title": "A < B & C",
            "url": "https://leetcode.com/problems/a-b/",
            "difficulty": "easy",
            "tags": "array & math",
            "reasoning": r"No raw \. or \- escapes",
            "coaching_hint": "x < y",
        }]
    )
    assert "A &lt; B &amp; C" in text
    assert "x &lt; y" in text
    assert r"\." not in text
    assert r"\-" not in text


def test_approval_callback_is_below_telegram_limit():
    keyboard = approval_keyboard("a" * 32)
    for button in keyboard["inline_keyboard"][0]:
        assert len(button["callback_data"].encode()) <= 64


def test_proposal_renderer_bounds_model_fields_without_breaking_html():
    text = render_proposal_html([
        {
            "title": "<&" * 500,
            "url": "https://leetcode.com/problems/example/",
            "difficulty": "medium",
            "tags": "<&" * 500,
            "reasoning": "<&" * 500,
            "coaching_hint": "<&" * 500,
        }
        for _ in range(5)
    ])

    assert "…" in text
    assert text.count("<blockquote>") == 5
    assert text.count("</blockquote>") == 5
