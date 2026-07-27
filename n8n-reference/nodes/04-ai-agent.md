# AI Agent node + model + tool sub-nodes

Root node type: `@n8n/n8n-nodes-langchain.agent`
Model sub-nodes: `@n8n/n8n-nodes-langchain.lmChatOpenAi`, `@n8n/n8n-nodes-langchain.lmChatGoogleGemini`
Docs:
- Agent: https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.agent
- OpenAI Chat Model: https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.lmchatopenai
- Google Gemini Chat Model: https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.lmchatgooglegemini

Used twice in this project — once in Flow A (propose 5 candidates with reasoning), once in Flow B (coach pass). Same root node type, different system prompt and tools.

## Cluster node shape — how the pieces connect

AI Agent is a **root node**. Models and tools are **sub-nodes** that attach to it via special connection types, not via `main`. Get this wrong and the canvas shows the sub-nodes floating disconnected.

```
AI Agent (root)
  ├── ai_language_model[0]  ← OpenAI Chat Model (primary)
  ├── ai_language_model[1]  ← Google Gemini Chat Model (fallback, only used when needsFallback=true)
  ├── ai_tool               ← HTTP Request Tool (YouTube search, etc.)
  ├── ai_tool               ← Custom Code Tool
  └── ai_tool               ← Call n8n Workflow Tool (optional, for DB access)
```

In the `connections` object this looks like:

```json
"connections": {
  "OpenAI Chat Model (gpt-5.6-sol)": {
    "ai_language_model": [
      [
        { "node": "AI Agent (propose 5)", "type": "ai_language_model", "index": 0 }
      ]
    ]
  },
  "Google Gemini Chat Model (flash fallback)": {
    "ai_language_model": [
      [
        { "node": "AI Agent (propose 5)", "type": "ai_language_model", "index": 1 }
      ]
    ]
  },
  "HTTP Request Tool (YouTube search)": {
    "ai_tool": [
      [
        { "node": "AI Agent (propose 5)", "type": "ai_tool", "index": 0 }
      ]
    ]
  }
}
```

Note the direction: the **sub-node** is the source, the **root** is the target. This is the opposite of `main` connections and trips up everyone the first time.

## Fallback model wiring

n8n's AI Agent supports a primary model and an optional fallback model. The fallback is **not** automatic — you enable it on the root node with the `needsFallback` toggle, and you connect the fallback model sub-node to `ai_language_model` **input index 1** (the primary stays at index 0). When the primary errors and `needsFallback` is on, n8n retries the same call against the model connected at index 1.

Two things to get right:

1. **`needsFallback: true`** in the root node's `options` (see the root node configs below). Without this toggle, n8n ignores the second model input even if it's wired.
2. **Fallback model at `ai_language_model` index 1.** If you wire both models to index 0 the canvas will show two connections on the same input and the fallback never fires — only the first-connected model runs.

The fallback is per-call, not per-session: if OpenAI 429s on the proposal prompt, n8n immediately retries the same prompt on Gemini. The agent's output shape is identical regardless of which model answered (both follow the prompt's JSON schema).

## Root node — Flow A (propose 5 candidates with reasoning + coaching hints)

```json
{
  "type": "@n8n/n8n-nodes-langchain.agent",
  "typeVersion": 1.8,
  "name": "AI Agent (propose 5)",
  "position": [480, 300],
  "parameters": {
    "promptType": "define",
    "text": "=You are my LeetCode coach. Today is {{ $now.toFormat('yyyy-MM-dd') }}.\n\nMy recent activity (leetcode_log, last 30 rows):\n{{ JSON.stringify($('Data Table (get recent log)').all().map(i => i.json), null, 2) }}\n\nProblems I've solved (leetcode_problems.solved = true):\n{{ JSON.stringify($('Data Table (get solved)').all().map(i => i.json), null, 2) }}\n\nActive lessons I'm reinforcing (tutor_lessons):\n{{ JSON.stringify($('Data Table (get active lessons)').all().map(i => i.json), null, 2) }}\n\nPropose 5 candidate problems for today. The student will pick 2 (typically 1 hard + 1 medium), so the 5 should include a mix of 2-3 hard and 2-3 medium.\n\nBias selection toward:\n- categories I'm weak in (check active tutor_lessons for patterns I'm reinforcing)\n- problems that exercise an active lesson (each candidate should target at least one active lesson where possible)\n- at most 1 problem I've already solved (for spaced repetition)\n- difficulty calibration: if my recent log shows I'm struggling on mediums (multiple skipped/reviewed), lean toward easier mediums; if I'm crushing mediums, lean harder\n\nFor EACH candidate, provide:\n- `reasoning`: 1-2 sentences explaining why this problem was chosen for me specifically. Reference my data — which weak pattern it targets, which active lesson it exercises, why the difficulty is appropriate. Not generic ("good practice for DP") — specific ("targets your 'off-by-one on inclusive bounds' lesson; medium because you're 4-for-6 on mediums this week").\n- `coaching_hint`: 1-line personalized note drawn from my active lessons. This will be shown in the per-problem Telegram message and stored in the Google Task. Example: \"last time you used a nested loop where a hashmap would do — before writing code, ask: can I trade space for time?\"\n\nOutput a JSON object with exactly two fields:\n1. `candidate_list_markdown` — a numbered MarkdownV2 string, each entry TWO lines:\n   `N. *Title* — tags — difficulty — URL`\n   `   Why: <reasoning>`\n   `   Hint: <coaching_hint>`\n   This is what gets sent to Telegram.\n2. `candidates` — a JSON array of 5 objects, one per entry above, in the same order. Each object must have exactly these keys:\n   - `slug` (string, URL slug like `two-sum`)\n   - `title` (string)\n   - `url` (string, full https://leetcode.com/problems/<slug>/ URL)\n   - `tags` (string, comma-separated)\n   - `difficulty` (string, one of `easy` / `medium` / `hard`)\n   - `reasoning` (string, 1-2 sentences, why this problem for me)\n   - `coaching_hint` (string, 1 line, personalized note from active lessons)\n\nDo not include any other text. Return ONLY the JSON object. No prose. No markdown code fences.",
    "options": {
      "systemMessage": "You are a LeetCode coach for a final-year CS student targeting Google and top fintechs. Be terse. Never invent problem titles or URLs — use the YouTube search tool if you need to confirm a problem exists. Your reasoning must reference the student's actual data (lessons, log), not generic advice.",
      "needsFallback": true
    }
  }
}
```

Field reference:
- `promptType: "define"` — explicit prompt text. The alternative is "auto" which uses the input item as the prompt; we don't want that here.
- `text`: the user prompt. Pulls data from upstream Data Table nodes via `$('Node Name').first().json`. The agent sees the rows as text.
- `options.systemMessage`: sets the agent's role. Keep it short — long system messages burn tokens on every call. The "reference the student's actual data" clause prevents generic reasoning like "good for practicing DP" when the student has no DP lessons.
- `options.needsFallback: true` — enables the fallback model input. Without this, the Gemini sub-node connected at `ai_language_model[1]` is inert.

Why both `candidate_list_markdown` and `candidates`: the markdown string is what `Telegram (send list)` ships to your chat (human-readable, numbered, with reasoning visible). The `candidates` array is what `Code (parse selection)` reads when you reply with "2 5" — it indexes into the array by number. Emitting both in one call keeps the agent's output as the single source of truth for both the display and the parser. If you emitted only the markdown, the Code node would have to regex-parse the string back into structured data — fragile, and it breaks on any title containing `—`.

Why `reasoning` and `coaching_hint` are separate fields: reasoning explains why the problem was *selected* (backward-looking, references your data); coaching_hint tells you what to *do* when you start (forward-looking, references a specific lesson). They serve different purposes and appear in different places: both in the Telegram list, but only `coaching_hint` in the per-problem message and Google Task notes (reasoning would be redundant there since you already picked it).

## Root node — Flow B (coach pass)

```json
{
  "type": "@n8n/n8n-nodes-langchain.agent",
  "typeVersion": 1.8,
  "name": "AI Agent (coach pass)",
  "position": [820, 300],
  "parameters": {
    "promptType": "define",
    "text": "=Problem: {{ $('Code (correlate reply)').first().json.problem_title }}\nURL: {{ $('Code (correlate reply)').first().json.problem_url }}\nExpected difficulty: {{ $('Code (correlate reply)').first().json.difficulty }}\nProblem tags: {{ $('Code (correlate reply)').first().json.tags }}\n\nUser's submission:\n{{ $('Telegram Trigger (incoming reply)').first().json.message.text }}\n\nActive lessons (check if this submission demonstrates or violates any of these):\n{{ JSON.stringify($('Data Table (get active lessons)').all().map(i => i.json), null, 2) }}\n\nIf the user pasted code, provide a COACHING review, not just a grade:\n1. Correctness: does it work? What edge case breaks it? Be honest — false praise costs interviews.\n2. Complexity: time/space in Big-O. Is that optimal for this pattern? If not, what is?\n3. Style/idiom: language-specific notes.\n4. Pattern coaching: what pattern/category does this problem exercise? How does it connect to the student's active lessons? If they demonstrated an active lesson correctly, say so explicitly (\"you correctly checked empty input before binary search — that's your lesson working\"). If they violated one, point it out (\"you nested two loops where a hashmap would do — this is the same pattern as your 'over-uses nested loops' lesson\").\n5. Next step: one concrete recommendation. What to study next, what problem to try, or \"you've got this pattern, move on to harder variants.\" If this is a basic version of a pattern, name the next-level problem (e.g. \"this is basic monotonic stack; next: 'largest rectangle in histogram'\").\n\nIf they pasted a status note (e.g., 'skipped', 'saw solution'): log status only, no review. But if they say 'saw solution', add one line: what the key insight was that they should take away.\n\nADAPTABILITY — lesson decision:\nDecide whether a generalizable lesson surfaced. A lesson is generalizable if it's a pattern that applies to multiple problems, not a one-off bug. Examples of generalizable lessons: \"forgets base case in recursion\", \"off-by-one on inclusive/exclusive bounds\", \"over-uses nested loops where a hashmap would do\", \"doesn't consider empty input before binary search\". Examples of NOT generalizable: \"typo in variable name\", \"forgot to return the result\".\n\nIf an existing active lesson matches (by title similarity or same category + same pattern), set lesson_is_recurring=true and the Code node will bump times_reinforced instead of creating a duplicate.\n\nIf an existing active lesson has times_reinforced >= 5 AND the student demonstrated it correctly this time, set lesson_should_graduate=true. The coach feedback should say: \"Retiring lesson: <title> — you've demonstrated this pattern consistently.\" The Code node will set active=false on that lesson.\n\nOutput JSON with these fields:\n- `tutor_feedback`: HTML-formatted coaching feedback for the user (all 5 sections above). If a lesson was saved, end with: 'Saved lesson: <b><lesson title></b>.' If reinforced: 'Reinforcing lesson: <b><lesson title></b> (Nth time).' If graduated: 'Retiring lesson: <b><lesson title></b> — demonstrated consistently.'\n- `lesson_title`: short title if a new lesson surfaced, else empty string\n- `lesson_category`: short category slug for the lesson (e.g. `binary-search`, `dp`, `graphs`, `two-pointers`, `hash-map`, `heap`, `backtracking`, `greedy`, `design`). Empty string if no lesson. This is what `Code (lesson decision)` reads to file a new lesson under `tutor_lessons.category`.\n- `lesson_is_recurring`: true if this matches an existing active lesson, else false\n- `lesson_should_graduate`: true if an existing lesson has been reinforced 5+ times AND the student demonstrated it correctly this time, else false\n- `solved`: true if the code is correct, false otherwise\n- `status`: 'solved' | 'reviewed' | 'skipped' | 'saw_solution'\n- `next_step`: one concrete recommendation (string). What to study, what problem to try next, or 'pattern mastered, move on.'\n\nReturn ONLY the JSON object. No prose. No markdown code fences.",
    "options": {
      "systemMessage": "You are a LeetCode coach, not just a grader. Be honest about wrong answers — false praise costs the user interviews. Cite complexity in Big-O. If you can't verify correctness from the code alone, say so. Always connect feedback to the student's active lessons when relevant — that's the adaptability loop that makes this system smarter over time.",
      "needsFallback": true
    }
  }
}
```

Why `lesson_category` is required: `Code (lesson decision)` reads `tutor.lesson_category` (with a `'general'` fallback) when filing a new lesson into `tutor_lessons`. If the agent omits the field, every new lesson lands in `general` and the `category`-based selection bias in Flow A stops working. The category slug is short on purpose — it's a key, not prose.

Why `lesson_should_graduate` is new in v3: without a graduation mechanism, `tutor_lessons` accumulates forever and the student keeps getting the same coaching hints even after they've mastered the pattern. The graduation rule (5+ reinforcements + correct demonstration) is conservative — it won't retire a lesson after a single lucky correct answer. The coach feedback explicitly announces the retirement so the student sees the system adapting.

Why `next_step` is new in v3: coaching without a next step is just critique. The next_step field forces the coach to always answer "so what should I do now?" — one concrete recommendation, not a vague "keep practicing." This is what separates a coach from a grader.

## Sub-node — OpenAI Chat Model (primary, both flows)

```json
{
  "type": "@n8n/n8n-nodes-langchain.lmChatOpenAi",
  "typeVersion": 1.6,
  "name": "OpenAI Chat Model (gpt-5.6-sol)",
  "position": [320, 200],
  "parameters": {
    "model": "gpt-5.6-sol",
    "options": {
      "temperature": 0.4,
      "maxTokens": 2000
    }
  },
  "credentials": {
    "openAiApi": {
      "id": "__CREDENTIAL_ID__",
      "name": "OpenAI — gpt-5.6"
    }
  }
}
```

Field reference:
- `model`: must be a model your OpenAI account has access to. n8n dynamically loads the list — if `gpt-5.6-sol` doesn't appear in the dropdown, your account doesn't have it. Don't type it manually if the dropdown is empty; it'll fail at runtime.
- `options.temperature`: 0.4 for coaching — low enough that the agent doesn't invent problem titles, high enough that it varies which 5 it proposes day to day.
- `options.maxTokens`: 2000 is enough for the 5-candidate list and for tutor feedback. Bump to 4000 if you find feedback getting cut off.
- **Do not toggle "Use Responses API"** unless you've tested it. Chat Completions is the default and is what the agent node expects. Responses API changes tool-calling semantics.

## Sub-node — Google Gemini Chat Model (fallback, both flows)

```json
{
  "type": "@n8n/n8n-nodes-langchain.lmChatGoogleGemini",
  "typeVersion": 1.4,
  "name": "Google Gemini Chat Model (flash fallback)",
  "position": [320, 380],
  "parameters": {
    "model": "gemini-3.6-flash",
    "options": {
      "temperature": 0.4,
      "maxOutputTokens": 2000
    }
  },
  "credentials": {
    "googleGeminiApi": {
      "id": "__CREDENTIAL_ID__",
      "name": "Google Gemini — flash"
    }
  }
}
```

Field reference:
- `model`: `gemini-3.6-flash`. Same dropdown caveat as OpenAI — must appear in the list.
- `options.maxOutputTokens`: note the different field name from OpenAI's `maxTokens`. Gemini uses `maxOutputTokens`.
- The Gemini sub-node has no proxy support per n8n docs. If your homelab routes through a proxy, you'll need a reverse proxy pointing at Gemini's host. Not a problem for direct egress.
- **Connect this sub-node to `ai_language_model` index 1 on the root agent**, not index 0. Index 0 is the primary (OpenAI). See the fallback wiring section above.

## Sub-node — HTTP Request Tool (used in Flow A for YouTube search)

```json
{
  "type": "@n8n/n8n-nodes-langchain.toolHttpRequest",
  "typeVersion": 1.1,
  "name": "HTTP Request Tool (YouTube search)",
  "position": [320, 560],
  "parameters": {
    "name": "youtube_search",
    "description": "Search YouTube for LeetCode problem walkthroughs. Input: problem title. Returns: top 3 video titles and URLs.",
    "method": "GET",
    "url": "https://www.googleapis.com/youtube/v3/search",
    "sendQuery": true,
    "queryParameters": {
      "parameters": [
        { "name": "part", "value": "snippet" },
        { "name": "maxResults", "value": "3" },
        { "name": "q", "value": "={{ $fromAI('query', 'LeetCode problem title') }}" },
        { "name": "type", "value": "video" },
        { "name": "key", "value": "{{YOUTUBE_API_KEY}}" }
      ]
    },
    "responseFormat": "text"
  }
}
```

Field reference:
- `name` and `description`: the agent sees these. Write the description so the agent knows **when** to call it and **what** to pass. `$fromAI('query', '...')` is n8n's syntax for "the agent fills this in."
- `responseFormat: "text"` — the agent reads the raw JSON. Don't use "json" here; the agent handles text better than structured for tool outputs.

## Sub-node — Custom Code Tool (used in Flow B for lesson lookup)

```json
{
  "type": "@n8n/n8n-nodes-langchain.toolCode",
  "typeVersion": 1.1,
  "name": "Custom Code Tool (lesson similarity)",
  "position": [320, 720],
  "parameters": {
    "name": "check_lesson_similarity",
    "description": "Check whether a candidate lesson title matches an existing active lesson. Input: lesson_title (string). Returns: matching lesson title or 'no match'.",
    "jsCode": "const title = $input.first().json.query.toLowerCase();\nconst existing = $('Data Table (get active lessons)').all().map(i => i.json);\nconst match = existing.find(r => r.title.toLowerCase().includes(title) || title.includes(r.title.toLowerCase()));\nreturn [{ json: { result: match ? match.title : 'no match' } }];"
  }
}
```

Use this when the agent wants to decide `lesson_is_recurring` itself instead of guessing. Optional — you can also just have the agent decide from the lessons text in the prompt.

## Settings tab (root node)

- **Retry On Fail**: on, 2 tries, 5000ms wait. LLM APIs 429 and 503 regularly.
- **On Error**: `continue (using error output)`. Wire error output to a Telegram "agent failed" message. Without this, an OpenAI outage silently drops the daily proposal and you don't notice until you check the executions list.

## Sub-node expression caveat

Per n8n docs: in sub-nodes, expressions always resolve to the **first item** of the input. If the AI Agent is processing 3 items (3 chosen problems), the model sub-node still sees only item 0. This is fine for our use case — the model name is fixed, not per-item — but don't try to make `model` an expression like `={{ $json.preferred_model }}`. It won't iterate.

## Common issue: agent returns prose instead of JSON

The model ignored the "output JSON" instruction. Two fixes:
1. Add `"Return ONLY the JSON object. No prose. No markdown code fences."` to the end of the prompt. (Already in both prompts above.)
2. Switch to OpenAI's **Responses API** with `response_format: { type: "json_object" }` — but only if you've tested it with the agent node, per the caveat above.

Start with fix 1; it usually works.

## Common issue: fallback never fires even though Gemini is wired

Three causes, in order of likelihood:
1. `needsFallback` is not set to `true` on the root node's `options`. Without the toggle, n8n treats the second model input as decorative.
2. Gemini is wired to `ai_language_model` index 0 (same as OpenAI). n8n only runs the model at index 0; index 1 is the fallback slot. Rewire to index 1.
3. OpenAI is succeeding but producing bad output (prose instead of JSON). Fallback only triggers on **errors** (429, 503, timeouts), not on bad-but-valid completions. For bad output, see the previous section.
