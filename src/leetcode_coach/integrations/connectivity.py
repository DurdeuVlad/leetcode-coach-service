"""Lightweight connectivity probes for every external service.

Each probe does the cheapest possible authenticated round-trip:
- Telegram:  GET /getMe  (returns the bot identity)
- OpenAI:    POST /chat/completions with max_tokens=1 (cheapest call)
- Gemini:    POST generateContent with max_output_tokens=1
- Browserless: GET /  (the /function endpoint requires a body; root is enough
              to confirm the instance is up)
- SearXNG:   GET /search?q=test&format=json  (one cheap query)

Mock/disabled services are reported as ``"mock"`` / ``"disabled"`` — they are
not probed. Probes never raise; a failed probe returns ``"unreachable: <msg>"``
so the caller (``/health``) can render the full picture without try/except
boilerplate.

Designed to be called from ``/health`` (HTTP) or the terminal simulator's
``:ping`` meta-command. Each probe has a 10s hard timeout so a hung service
can't stall the health check.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
import structlog

from leetcode_coach.config import get_settings

log = structlog.get_logger("connectivity")

_PROBE_TIMEOUT = 10.0


@dataclass
class ProbeResult:
    """One service probe outcome. `detail` carries error text or a short id."""

    name: str
    status: str  # "ok" | "mock" | "disabled" | "unreachable"
    detail: str = ""


async def _probe_telegram() -> ProbeResult:
    s = get_settings()
    token = s.telegram_bot_token
    if not token or token == "mock":
        return ProbeResult("telegram", "mock")
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
            r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
        if r.status_code == 200:
            bot = r.json().get("result", {}).get("username", "?")
            return ProbeResult("telegram", "ok", f"@{bot}")
        return ProbeResult("telegram", "unreachable", f"HTTP {r.status_code}")
    except Exception as e:
        return ProbeResult("telegram", "unreachable", str(e)[:120])


async def _probe_openai() -> ProbeResult:
    s = get_settings()
    key = s.openai_api_key
    if not key or key == "mock":
        return ProbeResult("openai", "mock")
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
            r = await c.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": s.openai_model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
        if r.status_code == 200:
            return ProbeResult("openai", "ok", s.openai_model)
        return ProbeResult("openai", "unreachable", f"HTTP {r.status_code}")
    except Exception as e:
        return ProbeResult("openai", "unreachable", str(e)[:120])


async def _probe_gemini() -> ProbeResult:
    s = get_settings()
    key = s.gemini_api_key
    if not key or key == "mock":
        return ProbeResult("gemini", "mock")
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=s.gemini_model,
                contents="ping",
                config=types.GenerateContentConfig(max_output_tokens=1),
            ),
            timeout=_PROBE_TIMEOUT,
        )
        # Any non-exception response means the key + model are valid.
        return ProbeResult("gemini", "ok", s.gemini_model)
    except asyncio.TimeoutError:
        return ProbeResult("gemini", "unreachable", "timeout")
    except Exception as e:
        return ProbeResult("gemini", "unreachable", str(e)[:120])


async def _probe_browserless() -> ProbeResult:
    s = get_settings()
    url = s.browserless_url
    if not url or url == "mock":
        return ProbeResult("browserless", "disabled")
    base = url.rstrip("/")
    if s.browserless_token:
        sep = "&" if "?" in base else "?"
        base = f"{base}{sep}token={s.browserless_token}"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
            r = await c.get(base)
        if r.status_code < 500:
            return ProbeResult("browserless", "ok", f"HTTP {r.status_code}")
        return ProbeResult("browserless", "unreachable", f"HTTP {r.status_code}")
    except Exception as e:
        return ProbeResult("browserless", "unreachable", str(e)[:120])


async def _probe_searxng() -> ProbeResult:
    s = get_settings()
    url = s.searxng_url
    if not url or url == "mock":
        return ProbeResult("searxng", "disabled")
    base = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
            r = await c.get(
                f"{base}/search",
                params={"q": "test", "format": "json", "engines": "youtube"},
            )
        if r.status_code == 200:
            return ProbeResult("searxng", "ok", "json ok")
        return ProbeResult("searxng", "unreachable", f"HTTP {r.status_code}")
    except Exception as e:
        return ProbeResult("searxng", "unreachable", str(e)[:120])


# Order is deliberate: inbound first, then primary LLM, fallback LLM, then
# the optional homelab services. The health endpoint renders them in this
# order so an operator scans top-to-bottom for the broken link.
_PROBES = (
    _probe_telegram,
    _probe_openai,
    _probe_gemini,
    _probe_browserless,
    _probe_searxng,
)


async def ping_all() -> list[ProbeResult]:
    """Run every probe concurrently and return results in declared order.

    Never raises — each probe catches its own errors and returns an
    ``unreachable`` result. Safe to call from ``/health``.
    """
    return await asyncio.gather(*(_probe() for _probe in _PROBES))


def render_probe_table(results: list[ProbeResult]) -> str:
    """Render results as a compact aligned table for terminal/CLI output."""
    name_w = max(len(r.name) for r in results)
    lines = []
    for r in results:
        detail = f" — {r.detail}" if r.detail else ""
        lines.append(f"  {r.name:<{name_w}}  {r.status}{detail}")
    return "\n".join(lines)
