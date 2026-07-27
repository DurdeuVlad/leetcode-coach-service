# Google Tasks node + OAuth2 credential (with the 7-day fix)

Node type: `n8n-nodes-base.googleTasks`
Docs: https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.googletasks
Credential docs: https://docs.n8n.io/integrations/builtin/credentials/google/oauth-single-service

Used in Flow B's pick branch (create task per chosen problem), in Flow B's coach branch (mark task complete + add notes), and in the Flow A expiry sweep (add "expired without reply" note). Same node type, different operations.

## Operations used

| Operation | Where | Purpose |
| --- | --- | --- |
| `task.create` | Flow B pick branch | Create one Google Task per chosen problem |
| `task.update` | Flow B coach branch | Mark task complete, add notes with coach feedback |
| `task.update` | Flow A expiry | Add note "expired without reply" (don't mark complete) |

## The OAuth2 root cause — read this first

The "Google OAuth randomly disconnects" symptom is **not random.** It's the consent screen status.

While the Google Cloud OAuth consent screen is in **Testing** status:
- Refresh tokens expire after **7 days**.
- n8n's credential silently goes stale, the next Google Tasks call 401s, and the workflow fails.

Fix (one-time, 5 minutes):
1. Google Cloud Console → APIs & Services → **OAuth consent screen**.
2. Click **Publish to production**. (You don't need verification — Google Tasks is a non-sensitive scope.)
3. Confirm the publishing status shows "In production."
4. In n8n, re-auth the Google Tasks credential once (the existing refresh token was issued under Testing status; re-auth gets a fresh one under Production status).
5. From now on, refresh tokens last indefinitely unless you revoke them.

Verify it worked: after 8 days, check the workflow executions. If Google Tasks calls still succeed, the fix held.

## Credential setup (one-time)

Self-hosted n8n can't use Managed OAuth2 (that's n8n Cloud only). You need a custom OAuth2 single-service credential.

In Google Cloud Console:
1. Create or pick a project.
2. APIs & Services → Library → enable **Google Tasks API**.
3. APIs & Services → Credentials → Create credentials → OAuth client ID.
4. Application type: **Web application**.
5. Add authorized redirect URI: `http://<your-n8n-host>/rest/oauth2-credential/callback`. For homelab without a domain, `http://localhost:5678/rest/oauth2-credential/callback` works — Google allows localhost for development.
6. Copy Client ID and Client Secret.

In n8n:
1. Credentials → New → Google Tasks OAuth2 API.
2. Paste Client ID, Client Secret.
3. Click "Sign in with Google" → authorize.
4. Name it exactly `Google Tasks — personal` (matches the workflow JSON).

Scopes n8n requests automatically: `https://www.googleapis.com/auth/tasks` (read/write tasks). Don't add more — fewer scopes, less verification friction.

## Node config — create task (Flow B pick branch, inside the per-problem loop)

```json
{
  "type": "n8n-nodes-base.googleTasks",
  "typeVersion": 1.1,
  "name": "Google Tasks (create per-problem)",
  "position": [1100, 460],
  "parameters": {
    "resource": "task",
    "operation": "create",
    "taskListId": {
      "__rl": true,
      "value": "{{TASKLIST_ID}}",
      "mode": "id"
    },
    "title": "=LeetCode: {{ $json.title }}",
    "notes": "=URL: {{ $json.url }}\nDifficulty: {{ $json.difficulty }}\nTags: {{ $json.tags }}",
    "additionalFields": {
      "due": "={{ $now.plus({ days: 1 }).toISO() }}"
    }
  },
  "credentials": {
    "googleTasksOAuth2Api": {
      "id": "__CREDENTIAL_ID__",
      "name": "Google Tasks — personal"
    }
  }
}
```

Field reference:
- `taskListId`: the ID of the Google Task list to write into. Get it once: add a temporary Google Tasks node with `operation: getTaskLists`, run, copy the ID, paste here. Use `mode: "id"` with `__rl: true` (n8n's resource-locator syntax).
- `title`: prefix with `LeetCode:` so tasks group visually in Google Tasks UI.
- `notes`: problem metadata so you can open the task and click through without going back to Telegram.
- `additionalFields.due`: due tomorrow 09:05 — soft deadline aligned with the expiry sweep.

The node outputs the created task including `id` (the Google Task ID). Capture it and store in `pending_review.google_task_id` so Flow B can update the right task.

## Node config — mark complete + add notes (Flow B end)

```json
{
  "type": "n8n-nodes-base.googleTasks",
  "typeVersion": 1.1,
  "name": "Google Tasks (mark complete)",
  "position": [1600, 460],
  "parameters": {
    "resource": "task",
    "operation": "update",
    "taskListId": {
      "__rl": true,
      "value": "{{TASKLIST_ID}}",
      "mode": "id"
    },
    "taskId": {
      "__rl": true,
      "value": "={{ $('Code (correlate reply)').first().json.google_task_id }}",
      "mode": "id"
    },
    "updateFields": {
      "status": "completed",
      "notes": "={{ 'Coach feedback:\n' + $('AI Agent (coach pass)').first().json.tutor_feedback + '\n\nLesson: ' + ($('AI Agent (coach pass)').first().json.lesson_title || 'none') + '\n\nNext: ' + ($('AI Agent (coach pass)').first().json.next_step || '') }}"
    }
  },
  "credentials": {
    "googleTasksOAuth2Api": {
      "id": "__CREDENTIAL_ID__",
      "name": "Google Tasks — personal"
    }
  }
}
```

Field reference:
- `taskId`: pulled from `pending_review.google_task_id` via the Code node that correlated the reply. Never hardcode — task IDs are opaque strings.
- `updateFields.status: "completed"` — Google Tasks marks the task done with a strikethrough.
- `updateFields.notes`: **appends, doesn't replace.** Google Tasks API behavior: `notes` in an update overwrites. To append, read the existing notes first (separate `get` operation) and concatenate. For v2 simplicity, just overwrite with the tutor feedback — the original URL/difficulty is in the task title anyway.

## Node config — expiry note (Flow A expiry sweep)

```json
{
  "type": "n8n-nodes-base.googleTasks",
  "typeVersion": 1.1,
  "name": "Google Tasks (expiry note)",
  "position": [820, 700],
  "parameters": {
    "resource": "task",
    "operation": "update",
    "taskListId": {
      "__rl": true,
      "value": "{{TASKLIST_ID}}",
      "mode": "id"
    },
    "taskId": {
      "__rl": true,
      "value": "={{ $json.google_task_id }}",
      "mode": "id"
    },
    "updateFields": {
      "notes": "=Expired without reply on {{ $now.toFormat('yyyy-MM-dd') }}."
    }
  },
  "credentials": {
    "googleTasksOAuth2Api": {
      "id": "__CREDENTIAL_ID__",
      "name": "Google Tasks — personal"
    }
  }
}
```

Don't set `status: "completed"` here — an expired task isn't done, it's abandoned. Leaving it incomplete means it shows up in Google Tasks' "not done" view, which is correct.

## Settings tab (all Google Tasks nodes)

- **Retry On Fail**: on, 3 tries, 3000ms wait. Google's API 429s under load.
- **On Error**: `continue (using error output)`. Wire error output to a Telegram "Google Tasks write failed" message. A failed task write shouldn't kill the tutor feedback — the Telegram confirmation in Flow B should still send even if the Google Task update failed.

## Common issue: `401 invalid_grant` after a week

This is the Testing-status refresh token expiry. Apply the "In production" fix above. If it still happens after publishing, the most likely cause is the user account being outside the test-user list while still in Testing — but publishing to production removes that restriction entirely.

## Common issue: `403 forbidden` on task create

The OAuth consent screen is published but the Google Tasks API is disabled in the project, or the token doesn't have the `tasks` scope. Re-auth the credential in n8n — the consent screen will show the scope being requested. If it doesn't list Google Tasks, the credential type is wrong (recreate as Google Tasks OAuth2, not generic Google OAuth2).
