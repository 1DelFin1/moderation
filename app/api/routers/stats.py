from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import SessionDep, ModeratorDep
from app.schemas import StatsOverview, ModeratorStats
from app.services.moderation_queue_service import ModerationQueueService

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


@router.get("/overview", response_model=StatsOverview)
async def get_stats_overview(
    moderator: ModeratorDep,
    session: SessionDep,
    period: Annotated[Literal["today", "week", "month"], Query()] = "today",
):
    data = await ModerationQueueService.get_stats_overview(session, period=period)
    return StatsOverview(**data)


@router.get("/moderators", response_model=list[ModeratorStats])
async def get_moderator_stats(
    moderator: ModeratorDep,
    session: SessionDep,
    period: Annotated[Literal["today", "week", "month"], Query()] = "week",
):
    data = await ModerationQueueService.get_moderator_stats(session, period=period)
    return [ModeratorStats(**d) for d in data]
