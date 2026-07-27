# Schedule Trigger node

Node type: `n8n-nodes-base.scheduleTrigger`
Docs: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger

Used twice in this project — one in Flow A, one in the expiry sweep. Same node type, different configs.

## Why Schedule Trigger (not Cron or Interval)

Schedule Trigger is wall-clock aware and supports timezone. Cron Trigger is deprecated in favor of it. Interval Trigger fires every N seconds from activation time, which drifts and can't target "09:05 daily." Use Schedule Trigger for both.

## Node 1 — daily candidates trigger (Flow A start)

Fires once at 09:05 Bucharest every day. Triggers the AI Agent proposal step.

```json
{
  "type": "n8n-nodes-base.scheduleTrigger",
  "typeVersion": 1.2,
  "name": "Schedule Trigger (daily 09:05)",
  "position": [240, 300],
  "parameters": {
    "rule": {
      "interval": [
        {
          "field": "cronExpression",
          "expression": "5 9 * * *"
        }
      ]
    }
  }
}
```

Field reference:
- `rule.interval[0].field`: `"cronExpression"` is the most reliable mode. The other modes (`days`, `hours`) build a UI-driven schedule but are harder to diff in JSON.
- `expression`: standard 5-field cron. `5 9 * * *` = minute 5, hour 9, every day, every month, every weekday. Order is minute hour day-of-month month day-of-week.
- Timezone comes from workflow settings (`Europe/Bucharest`), not from this node. Do not add a `timezone` field here — it's ignored.

Output: one empty item at 09:05. Wire `main[0]` to the AI Agent.

## Node 2 — expiry sweep trigger

Fires once at 05:05 Bucharest every day — roughly 20 hours after the 09:05 candidates message, so anything not yet replied to is past its useful window.

```json
{
  "type": "n8n-nodes-base.scheduleTrigger",
  "typeVersion": 1.2,
  "name": "Schedule Trigger (expiry 05:05)",
  "position": [240, 700],
  "parameters": {
    "rule": {
      "interval": [
        {
          "field": "cronExpression",
          "expression": "5 5 * * *"
        }
      ]
    }
  }
}
```

This is a separate node in the same Flow A workflow (or a separate workflow — your call; same workflow is simpler to export as one file). Wire `main[0]` to the expiry sweep Code node, not to the AI Agent.

## Settings tab (both nodes)

- **Execute Once**: leave off. Schedule Trigger already fires once per tick; this setting is for nodes that process item lists.
- **Retry On Fail**: leave off on the trigger itself. If the trigger fails to fire, that's an n8n-instance problem, not a transient API failure. Put retry on the downstream nodes instead.

## Common issue: runs at the wrong time

If the trigger fires at the wrong hour, the cause is almost always timezone. Check in this order:
1. Workflow Settings → Timezone = `Europe/Bucharest` (this is what `settings.timezone` in the workflow JSON controls).
2. If unset, n8n falls back to `GENERIC_TIMEZONE` env var on the host. On the homelab, set `GENERIC_TIMEZONE=Europe/Bucharest` in the n8n docker-compose env so any new workflow defaults correctly.
3. Don't set both — workflow setting wins, but having them disagree is a footgun for future-you.

## Common issue: trigger doesn't fire at all

The workflow must be **Active** (top-right toggle in the n8n UI). Inactive workflows don't run on schedule even if the trigger is correctly configured. Check the executions list — if you see zero executions for that workflow, the workflow is inactive or n8n itself is down.
