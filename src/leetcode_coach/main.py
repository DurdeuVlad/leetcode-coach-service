"""FastAPI entry point for the LeetCode Coach service."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Header, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from leetcode_coach.application import CoachApplication
from leetcode_coach.config import get_settings
from leetcode_coach.db.base import create_db_engine
from leetcode_coach.domain.services import CoachDomain
from leetcode_coach.integrations.telegram import set_webhook
from leetcode_coach.scheduler import SchedulerHandle, start_scheduler, stop_scheduler

log = structlog.get_logger("webhook")
settings = get_settings()
engine = create_db_engine(settings.database_url)
coach = CoachApplication(engine)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Start the in-process APScheduler. The pg advisory lock ensures only
    # one scheduler instance fires jobs even if multiple containers run.
    # entrypoint.sh runs migrations before the app boots, so the schema
    # is ready by the time we get here.
    scheduler_handle: SchedulerHandle | None = None
    try:
        scheduler_handle = await start_scheduler()
    except Exception as e:
        log.error("scheduler_start_failed", error=str(e))

    if settings.telegram_webhook_url:
        try:
            await set_webhook(settings.telegram_webhook_url.rstrip("/") + "/telegram/webhook")
            log.info("webhook_registered", url=settings.telegram_webhook_url)
        except Exception as e:
            log.error("webhook_registration_failed", error=str(e))
    else:
        log.warning("webhook_skipped", reason="TELEGRAM_WEBHOOK_URL not set")

    try:
        yield
    finally:
        if scheduler_handle is not None:
            stop_scheduler(scheduler_handle)


app = FastAPI(title="LeetCode Coach", version="2.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1"))
    except Exception:
        return {"status": "degraded", "database": "down"}
    return {"status": "ok", "database": "up"}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
) -> Response:
    if settings.telegram_webhook_secret and (
        x_telegram_bot_api_secret_token is None
        or not secrets.compare_digest(
            x_telegram_bot_api_secret_token, settings.telegram_webhook_secret
        )
    ):
        return Response(status_code=200)
    try:
        payload = await request.json()
        update_id = int(payload["update_id"])
        callback = payload.get("callback_query")
        message = payload.get("message")
        if callback:
            chat_id = int(callback["message"]["chat"]["id"])
        elif message:
            chat_id = int(message["chat"]["id"])
        else:
            return Response(status_code=200)
    except (KeyError, TypeError, ValueError):
        log.warning("malformed_update")
        return Response(status_code=200)
    if str(chat_id) != str(settings.telegram_chat_id):
        return Response(status_code=200)

    with Session(engine) as session:
        domain = CoachDomain(session)
        if not domain.record_update(update_id, chat_id):
            status = domain.processed_update_status(update_id)
            return Response(status_code=503 if status == "received" else 200)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return Response(status_code=200)
    try:
        if callback:
            await coach.handle_callback(
                chat_id=chat_id,
                callback_id=str(callback["id"]),
                data=str(callback.get("data", "")),
            )
        else:
            text_value = message.get("text") or message.get("caption")
            if not text_value:
                return Response(status_code=200)
            reply = message.get("reply_to_message") or {}
            await coach.handle_text(
                chat_id=chat_id,
                text=str(text_value),
                message_id=int(message["message_id"]),
                reply_to_message_id=int(reply["message_id"]) if reply.get("message_id") else None,
            )
        with Session(engine) as session:
            CoachDomain(session).mark_update_handled(update_id)
            session.commit()
    except Exception as exc:
        log.exception("update_failed", update_id=update_id, error=str(exc))
        with Session(engine) as session:
            CoachDomain(session).mark_update_handled(update_id, str(exc))
            session.commit()
        return Response(status_code=503)
    return Response(status_code=200)
