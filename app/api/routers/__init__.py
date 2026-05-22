from fastapi import APIRouter

from app.api.routers.auth import router as auth_router
from app.api.routers.moderators import router as moderators_router
from app.api.routers.queue import router as queue_router
from app.api.routers.tickets import router as tickets_router
from app.api.routers.blocking_reasons import router as blocking_reasons_router
from app.api.routers.b2b_events import router as b2b_events_router
from app.api.routers.stats import router as stats_router

main_router = APIRouter()

main_router.include_router(auth_router)
main_router.include_router(moderators_router)
main_router.include_router(queue_router)
main_router.include_router(tickets_router)
main_router.include_router(blocking_reasons_router)
main_router.include_router(b2b_events_router)
main_router.include_router(stats_router)

__all__ = ["main_router"]
