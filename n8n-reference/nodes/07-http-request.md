# HTTP Request node (LeetCode GraphQL)

Node type: `n8n-nodes-base.httpRequest`
Docs: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest

Used in the weekly refresh flow (Flow W) to pull problem metadata from LeetCode's public GraphQL endpoint. Not used in Flow A or Flow B.

## Why HTTP Request, not the GraphQL node

n8n has a dedicated GraphQL node. For a single fixed query, HTTP Request with a JSON body is simpler — the GraphQL node adds a UI for variables you don't need to tweak at runtime. Use HTTP Request.

## LeetCode GraphQL endpoint

- URL: `https://leetcode.com/graphql`
- Method: `POST`
- Auth: none (public endpoint)
- Content-Type: `application/json`
- Body: `{"query": "...", "variables": {...}}`

LeetCode's GraphQL is unauthenticated for problem metadata. Rate limits are loose but exist — don't hammer it. Weekly refresh pulls ~50-200 problems in one run, which is fine.

## Node config — fetch problem list by slug

```json
{
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2,
  "name": "HTTP Request (LeetCode GraphQL)",
  "position": [480, 300],
  "parameters": {
    "method": "POST",
    "url": "https://leetcode.com/graphql",
    "sendHeaders": true,
    "headerParameters": {
      "parameters": [
        { "name": "Content-Type", "value": "application/json" },
        { "name": "User-Agent", "value": "leetcode-coach-n8n/2.0" }
      ]
    },
    "sendBody": true,
    "specifyBody": "json",
    "jsonBody": "={\n  \"query\": \"query getQuestion($titleSlug: String!) { question(titleSlug: $titleSlug) { title titleSlug difficulty url topicTags { name } } }\",\n  \"variables\": { \"titleSlug\": \"{{ $json.slug }}\" }\n}",
    "options": {
      "timeout": 10000,
      "response": {
        "response": {
          "fullResponse": false,
          "neverError": true
        }
      }
    }
  }
}
```

Field reference:
- `specifyBody: "json"` + `jsonBody`: the body is a JSON string. The `=` prefix makes it an expression so `{{ $json.slug }}` interpolates from the loop item.
- `headerParameters.User-Agent`: set a custom UA. LeetCode occasionally 403s default n8n UAs; a custom one avoids this.
- `options.timeout: 10000` — 10s. LeetCode's GraphQL is usually <2s; 10s is generous.
- `options.response.response.neverError: true` — don't throw on non-2xx; let the Code node downstream inspect the status. This lets you handle 429s and 404s (slug typo) gracefully instead of killing the workflow.

## Looping over slugs

This node sits inside a loop driven by an upstream Code node that outputs `[{json:{slug:"two-sum"}}, {json:{slug:"..."}}, ...]`. Each item triggers one HTTP Request. n8n runs them sequentially by default — fine for 200 items at ~1s each.

To parallelize, set the loop's batch size in the upstream Code node or use the Split In Batches node with `batchSize: 5`. Not worth it for weekly refresh; sequential is simpler and avoids rate limits.

## Handling the response

The response shape:

```json
{
  "data": {
    "question": {
      "title": "Two Sum",
      "titleSlug": "two-sum",
      "difficulty": "Easy",
      "url": "/problems/two-sum/",
      "topicTags": [{ "name": "Array" }, { "name": "Hash Table" }]
    }
  }
}
```

Wire the HTTP Request output to a Code node that:
1. Checks `json.data.question` exists (LeetCode returns `data.question: null` for bad slugs).
2. Flattens `topicTags` to a comma string: `json.data.question.topicTags.map(t => t.name.toLowerCase().replace(/\s+/g, '-')).join(',')`.
3. Normalizes `difficulty` to lowercase.
4. Prefixes `url` with `https://leetcode.com` if it starts with `/`.

Then feed the Code node's output into a Data Table `row.upsert` against `leetcode_problems`.

## Settings tab

- **Retry On Fail**: on, 3 tries, 5000ms wait. LeetCode 429s under load; retry with backoff handles it.
- **On Error**: `continue (using error output)`. Wire error output to a Code node that logs the failed slug to an `error_log` data table and continues — one bad slug shouldn't kill the weekly refresh.

## Common issue: `400 Bad Request`

The GraphQL query is malformed. Test the query in LeetCode's GraphQL explorer (https://leetcode.com/graphql) before pasting into n8n. Common mistakes: missing `$` in variable references, wrong type names (`Question` vs `question` — LeetCode uses lowercase `question`).

## Common issue: `403 Forbidden`

You hit the rate limit or the UA was blocked. Two fixes:
1. Add `User-Agent: Mozilla/5.0` instead of the custom UA — LeetCode allows browser UAs.
2. Slow down: insert a Wait node (1s) between HTTP Request calls in the loop.

## Common issue: response is HTML, not JSON

LeetCode occasionally returns an HTML error page (CDN issue, maintenance). The `neverError: true` option prevents a crash; the Code node downstream should check `typeof json === 'object' && json.data` before accessing fields, and log "LeetCode returned HTML, retry later" if not.
