from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.deps import SessionDep, ModeratorDep
from app.schemas import (
    TicketResponse,
    PaginatedTickets,
    ClaimNextRequest,
)
from app.services.moderation_queue_service import ModerationQueueService

router = APIRouter(prefix="/api/v1/queue", tags=["moderation-queue"])


@router.get("", response_model=PaginatedTickets)
async def list_queue(
    moderator: ModeratorDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    queue_priority: Annotated[int | None, Query(ge=1, le=4)] = None,
    category_id: Annotated[UUID | None, Query()] = None,
    seller_id: Annotated[UUID | None, Query()] = None,
):
    items, total = await ModerationQueueService.get_queue(
        session,
        limit=limit,
        offset=offset,
        queue_priority=queue_priority,
        category_id=category_id,
        seller_id=seller_id,
    )
    return PaginatedTickets(
        items=[TicketResponse.model_validate(t) for t in items],
        total_count=total,
        limit=limit,
        offset=offset,
    )


@router.post("/claim", response_model=TicketResponse, responses={204: {"description": "Queue is empty"}})
async def claim_next(
    moderator: ModeratorDep,
    session: SessionDep,
    body: ClaimNextRequest | None = None,
):
    ticket = await ModerationQueueService.claim_next(
        session,
        moderator_id=moderator.id,
        queue_priority=body.queue_priority if body else None,
        category_ids=body.category_ids if body else None,
    )
    if ticket is None:
        return Response(status_code=204)
    return TicketResponse.model_validate(ticket)
