import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.database import async_session_factory
from app.services.moderation_queue_service import ModerationQueueService

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def auto_return_expired_tickets() -> None:
    try:
        async with async_session_factory() as session:
            count = await ModerationQueueService.auto_return_expired(session)
            if count:
                logger.info("Scheduler: auto-returned %d expired tickets", count)
    except Exception as exc:
        logger.error("Scheduler: auto_return_expired_tickets failed: %s", exc)


def configure_scheduler() -> AsyncIOScheduler:
    scheduler.add_job(
        auto_return_expired_tickets,
        "interval",
        minutes=5,
        id="auto_return_expired",
        replace_existing=True,
    )
    return scheduler
