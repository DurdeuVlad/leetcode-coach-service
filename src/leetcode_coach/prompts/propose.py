"""Flow A candidate-selection prompt — ported VERBATIM from the n8n AI Agent node.

Source: n8n-reference/workflows/flow-a-schedule-and-expiry.json
        node "AI Agent (propose 5)" `text` field (line 98) + `systemMessage`
        (line 100).

The n8n `{{ }}` template expressions are replaced with Python `{}` placeholders
that the flow fills in with real data. The prose is unchanged.

BUG-1 FIX (documented in docs/business-requirements.md FR-1.2):
The n8n source passed `solved = true` rows into the prompt labelled as
"Problems I've solved". Flow A never saw the unsolved pool, so the LLM could
only pick from already-solved problems. The Python port passes the UNSOLVED
pool (`solved = false`) instead, per FR-1.2. The label is changed from
"Problems I've solved" to "Unsolved problems I can pick from" to match what
the data actually is. This is the only prose change; everything else is
verbatim.
"""

from __future__ import annotations

# Verbatim from the n8n AI Agent node `options.systemMessage` (line 100).
PROPOSE_SYSTEM = (
    "You are a LeetCode coach for a final-year CS student targeting Google and "
    "top fintechs. Be terse. Never invent problem titles or URLs — use the "
    "YouTube search tool if you need to confirm a problem exists. Your "
    "reasoning must reference the student's actual data (lessons, log), not "
    "generic advice."
)

# Verbatim from the n8n AI Agent node `text` field (line 98), with:
#  - `{{ }}` → `{}`
#  - BUG-1 fix: "Problems I've solved (leetcode_problems.solved = true)" →
#    "Unsolved problems I can pick from (leetcode_problems.solved = false)"
# All other prose is unchanged.
PROPOSE_PROMPT = """You are my LeetCode coach. Today is {today}.

My recent activity (leetcode_log, last 30 rows):
{recent_log_json}

Unsolved problems I can pick from (leetcode_problems.solved = false):
{unsolved_pool_json}

Active lessons I'm reinforcing (tutor_lessons):
{active_lessons_json}

Propose 5 candidate problems for today. The student will pick 2 (typically 1 hard + 1 medium), so the 5 should include a mix of 2-3 hard and 2-3 medium.

Bias selection toward:
- categories I'm weak in (check active tutor_lessons for patterns I'm reinforcing)
- problems that exercise an active lesson (each candidate should target at least one active lesson where possible)
- at most 1 problem I've already solved (for spaced repetition)
- difficulty calibration: if my recent log shows I'm struggling on mediums (multiple skipped/reviewed), lean toward easier mediums; if I'm crushing mediums, lean harder

For EACH candidate, provide:
- `reasoning`: 1-2 sentences explaining why this problem was chosen for me specifically. Reference my data — which weak pattern it targets, which active lesson it exercises, why the difficulty is appropriate. Not generic ("good practice for DP") — specific ("targets your 'off-by-one on inclusive bounds' lesson; medium because you're 4-for-6 on mediums this week").
- `coaching_hint`: 1-line personalized note drawn from my active lessons. This will be shown in the per-problem Telegram message and stored in the Google Task. Example: "last time you used a nested loop where a hashmap would do — before writing code, ask: can I trade space for time?"

Output a JSON object with exactly two fields:
1. `candidate_list_markdown` — a numbered MarkdownV2 string, each entry TWO lines:
   `N. *Title* — tags — difficulty — URL`
   `   Why: <reasoning>`
   `   Hint: <coaching_hint>`
   This is what gets sent to Telegram.
2. `candidates` — a JSON array of 5 objects, one per entry above, in the same order. Each object must have exactly these keys:
   - `slug` (string, URL slug like `two-sum`)
   - `title` (string)
   - `url` (string, full https://leetcode.com/problems/<slug>/ URL)
   - `tags` (string, comma-separated)
   - `difficulty` (string, one of `easy` / `medium` / `hard`)
   - `reasoning` (string, 1-2 sentences, why this problem for me)
   - `coaching_hint` (string, 1 line, personalized note from active lessons)

Do not include any other text. Return ONLY the JSON object. No prose. No markdown code fences."""


# --- Output JSON contract (for #016 to parse against) ---
#
# The LLM returns a single JSON object with exactly two top-level keys:
#
#   {
#     "candidate_list_markdown": "1. *Two Sum* — array,hash-map — easy — https://...\n   Why: ...\n   Hint: ...\n2. ...",
#     "candidates": [
#       {
#         "slug": "two-sum",
#         "title": "Two Sum",
#         "url": "https://leetcode.com/problems/two-sum/",
#         "tags": "array,hash-map",
#         "difficulty": "easy",          # one of easy/medium/hard
#         "reasoning": "1-2 sentences...",
#         "coaching_hint": "1 line..."
#       },
#       ...exactly 5 objects, same order as the markdown list
#     ]
#   }
#
# Defensive validation in #016 enforces:
#   - exactly 5 candidates
#   - difficulty mix: 2-3 hard + 2-3 medium (FR-1.3), never 5 of one
#   - at most 1 already-solved (FR-1.2) — checked against the unsolved pool
#   - every candidate has all 7 required keys
#   - URLs match https://leetcode.com/problems/<slug>/ shape (FR-1.7)
