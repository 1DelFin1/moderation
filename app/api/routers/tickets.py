from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import SessionDep, ModeratorDep
from app.schemas import (
    ApproveRequest,
    BlockingReasonResponse,
    BlockDecisionRequest,
    FieldReportSchema,
    TicketResponse,
    TicketDetailResponse,
    PaginatedTickets,
)
from app.services.moderation_queue_service import ModerationQueueService

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])


@router.get("", response_model=PaginatedTickets)
async def list_tickets(
    moderator: ModeratorDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[Literal["PENDING", "IN_REVIEW", "APPROVED", "BLOCKED", "HARD_BLOCKED"] | None, Query()] = None,
    moderator_id: Annotated[UUID | None, Query()] = None,
    product_id: Annotated[UUID | None, Query()] = None,
    seller_id: Annotated[UUID | None, Query()] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
):
    items, total = await ModerationQueueService.list_tickets(
        session,
        limit=limit,
        offset=offset,
        status=status,
        moderator_id=moderator_id,
        product_id=product_id,
        seller_id=seller_id,
        created_from=created_from,
        created_to=created_to,
    )
    return PaginatedTickets(
        items=[TicketResponse.model_validate(t) for t in items],
        total_count=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(
    ticket_id: UUID,
    moderator: ModeratorDep,
    session: SessionDep,
):
    ticket = await ModerationQueueService.get_by_id(session, ticket_id)
    blocking_reasons = [
        BlockingReasonResponse.model_validate(r)
        for r in getattr(ticket, "_blocking_reasons", [])
    ]
    field_reports = [
        FieldReportSchema.model_validate(fr)
        for fr in (ticket.field_reports or [])
    ]
    response = TicketDetailResponse.model_validate(ticket)
    response.field_reports = field_reports
    response.blocking_reasons = blocking_reasons
    return response


@router.post("/{ticket_id}/release", response_model=TicketResponse)
async def release_ticket(
    ticket_id: UUID,
    moderator: ModeratorDep,
    session: SessionDep,
):
    is_admin = moderator.role == "ADMIN"
    ticket = await ModerationQueueService.release(
        session,
        ticket_id=ticket_id,
        moderator_id=moderator.id,
        is_admin=is_admin,
    )
    return TicketResponse.model_validate(ticket)


@router.post("/{ticket_id}/approve", response_model=TicketResponse)
async def approve_ticket(
    ticket_id: UUID,
    moderator: ModeratorDep,
    session: SessionDep,
    body: ApproveRequest | None = None,
):
    is_admin = moderator.role == "ADMIN"
    ticket = await ModerationQueueService.approve(
        session,
        ticket_id=ticket_id,
        moderator_id=moderator.id,
        comment=body.comment if body else None,
        is_admin=is_admin,
    )
    return TicketResponse.model_validate(ticket)


@router.post("/{ticket_id}/block", response_model=TicketResponse)
async def block_ticket(
    ticket_id: UUID,
    body: BlockDecisionRequest,
    moderator: ModeratorDep,
    session: SessionDep,
):
    is_admin = moderator.role == "ADMIN"
    ticket = await ModerationQueueService.block(
        session,
        ticket_id=ticket_id,
        moderator_id=moderator.id,
        blocking_reason_ids=body.blocking_reason_ids,
        comment=body.comment,
        field_reports=body.field_reports,
        is_admin=is_admin,
    )
    return TicketResponse.model_validate(ticket)
