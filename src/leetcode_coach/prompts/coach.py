"""Flow B coach pass prompt — ported VERBATIM from the n8n AI Agent node.

Source: n8n-reference/workflows/flow-b-telegram-and-coach.json
        node "AI Agent (coach pass)" `text` field (line 294) + `systemMessage`
        (line 296).

The n8n `{{ }}` template expressions are replaced with Python `{}` placeholders
that the flow fills in with real data. The prose is unchanged.

No BUG-N fixes here — this prompt is ported as-is. The two Flow B bugs
(BUG-2: Google Task notes append, and the lesson-graduation hallucination
risk) are handled in code (flow_b post-coach updates and the double-gate
graduation check), not in the prompt.
"""

from __future__ import annotations

# Verbatim from the n8n AI Agent node `options.systemMessage` (line 296).
COACH_SYSTEM = (
    "You are a LeetCode coach, not just a grader. Be honest about wrong "
    "answers — false praise costs the user interviews. Cite complexity in "
    "Big-O. If you can't verify correctness from the code alone, say so. "
    "Always connect feedback to the student's active lessons when relevant "
    "— that's the adaptability loop that makes this system smarter over time."
)

# Verbatim from the n8n AI Agent node `text` field (line 294), with:
#  - `{{ }}` → `{}`
# All prose is unchanged (em-dashes, JSON contract, everything).
COACH_PROMPT = """Problem: {problem_title}
URL: {problem_url}
Expected difficulty: {difficulty}
Problem tags: {tags}

User's submission:
{user_text}

Active lessons (check if this submission demonstrates or violates any of these):
{active_lessons_json}

If the user pasted code, provide a COACHING review, not just a grade:
1. Correctness: does it work? What edge case breaks it? Be honest — false praise costs interviews.
2. Complexity: time/space in Big-O. Is that optimal for this pattern? If not, what is?
3. Style/idiom: language-specific notes.
4. Pattern coaching: what pattern/category does this problem exercise? How does it connect to the student's active lessons? If they demonstrated an active lesson correctly, say so explicitly. If they violated one, point it out.
5. Next step: one concrete recommendation. What to study next, what problem to try, or "you've got this pattern, move on to harder variants."

If they pasted a status note (e.g., 'skipped', 'saw solution'): log status only, no review. But if they say 'saw solution', add one line: what the key insight was that they should take away.

ADAPTABILITY — lesson decision:
Decide whether a generalizable lesson surfaced. A lesson is generalizable if it's a pattern that applies to multiple problems, not a one-off bug.

If an existing active lesson matches (by title similarity or same category + same pattern), set lesson_is_recurring=true and the Code node will bump times_reinforced instead of creating a duplicate.

If an existing active lesson has times_reinforced >= 5 AND the student demonstrated it correctly this time, set lesson_should_graduate=true. The coach feedback should say: "Retiring lesson: <title> — you've demonstrated this pattern consistently."

Output JSON with these fields:
- tutor_feedback: plain-text coaching feedback for the user (all 5 sections above). Do NOT include any HTML tags. Do NOT include the lesson footer ('Saved lesson:', 'Reinforcing lesson:', 'Retiring lesson:') — the system appends it from the lesson decision fields below. Emit plain text only; the system wraps it in HTML and escapes special characters.
- lesson_title: short title if a new lesson surfaced, else empty string
- lesson_category: short category slug (binary-search, dp, graphs, two-pointers, hash-map, heap, backtracking, greedy, design). Empty string if no lesson.
- lesson_is_recurring: true if this matches an existing active lesson, else false
- lesson_should_graduate: true if an existing lesson has been reinforced 5+ times AND the student demonstrated it correctly this time, else false
- solved: true if the code is correct, false otherwise
- status: 'solved' | 'reviewed' | 'skipped' | 'saw_solution'
- next_step: one concrete recommendation (string)

Return ONLY the JSON object. No prose. No markdown code fences."""


# --- Output JSON contract (for #024 to parse against) ---
#
# The LLM returns a single JSON object with exactly these top-level keys:
#
#   {
#     "tutor_feedback": "<plain-text string, 5 sections, NO HTML, NO footer>",
#     "lesson_title": "<short title or empty string>",
#     "lesson_category": "<slug or empty string>",
#     "lesson_is_recurring": <bool>,
#     "lesson_should_graduate": <bool>,
#     "solved": <bool>,
#     "status": "solved" | "reviewed" | "skipped" | "saw_solution",
#     "next_step": "<one concrete recommendation>"
#   }
#
# Rendering decision (docs/telegram-formatting.md §3.2.3): `tutor_feedback`
# is plain text, NOT HTML. The code html.escape()s it and wraps it in
# <blockquote>; the lesson footer is built in code from the lesson decision
# fields (already escaped). The previous contract had the LLM emit HTML,
# which was not escaped before sending — a latent bug if the LLM reviewed
# code containing `<`, `>`, or `&`.
#
# Defensive validation in #024 enforces:
#   - all 8 keys present
#   - status is one of the 4 enum values
#   - lesson_category (when non-empty) is one of the 9 listed slugs
#   - lesson_should_graduate=true implies lesson_is_recurring=true
#     (you can't graduate a lesson you didn't match to an existing one)
#
# IMPORTANT — double-gated graduation (AGENTS.md gotcha #4):
#   The coach's `lesson_should_graduate` is necessary but NOT sufficient.
#   #025 must ALSO read `times_reinforced` from the DB row and check
#   `>= 5`. The coach hallucinating a count is a known failure mode.
#   The DB is the source of truth for the count, not the coach.
