"""Canonical product-moderation endpoints (moderation-flows.md).

Paths:
  POST /api/v1/product-moderation/get-next   — MOD-2 (claim next card)
  POST /api/v1/products/{product_id}/approve — MOD-3
  POST /api/v1/products/{product_id}/decline — MOD-4/5 (soft or hard block)
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import SessionDep, ModeratorDep
from app.schemas import (
    ApproveRequest,
    BlockDecisionRequest,
    ClaimNextRequest,
    DeclineRequest,
    FieldReportSchema,
    TicketResponse,
)
from app.services.moderation_queue_service import ModerationQueueService

# ----- /api/v1/product-moderation ----------------------------------------

product_moderation_router = APIRouter(
    prefix="/api/v1/product-moderation",
    tags=["product-moderation"],
)


@product_moderation_router.post(
    "/get-next",
    response_model=TicketResponse,
    responses={204: {"description": "Queue is empty"}},
)
async def get_next(
    moderator: ModeratorDep,
    session: SessionDep,
    body: ClaimNextRequest | None = None,
):
    """MOD-2: Claim the next pending card from the moderation queue.

    Body (optional): {"queueId": 1}  — 1..4, or null for auto-priority.
    Returns 200 with ticket or 204 when queue is empty.
    """
    queue_id = body.queue_priority if body else None
    ticket = await ModerationQueueService.claim_next(
        session,
        moderator_id=moderator.id,
        queue_priority=queue_id,
    )
    if ticket is None:
        return Response(status_code=204)
    return TicketResponse.model_validate(ticket)


# ----- /api/v1/products/{product_id}/... ---------------------------------

products_moderation_router = APIRouter(
    prefix="/api/v1/products",
    tags=["product-moderation"],
)


@products_moderation_router.post("/{product_id}/approve", response_model=TicketResponse)
async def approve_product(
    product_id: UUID,
    moderator: ModeratorDep,
    session: SessionDep,
    body: ApproveRequest | None = None,
):
    """MOD-3: Approve a product by product_id.

    Looks up the active ticket for the product, then approves it.
    Sends MODERATED event to B2B.
    """
    ticket = await ModerationQueueService.get_by_product_id(session, product_id)
    is_admin = moderator.role == "ADMIN"
    ticket = await ModerationQueueService.approve(
        session,
        ticket_id=ticket.id,
        moderator_id=moderator.id,
        comment=body.comment if body else None,
        is_admin=is_admin,
    )
    return TicketResponse.model_validate(ticket)


@products_moderation_router.post("/{product_id}/decline", response_model=TicketResponse)
async def decline_product(
    product_id: UUID,
    body: DeclineRequest,
    moderator: ModeratorDep,
    session: SessionDep,
):
    """MOD-4/5: Soft-block (BLOCKED) or hard-block (HARD_BLOCKED) a product by product_id.

    If the blocking_reason has hard_block=true → status becomes HARD_BLOCKED (MOD-5).
    Sends BLOCKED event to B2B with hard_block flag.
    """
    ticket = await ModerationQueueService.get_by_product_id(session, product_id)
    is_admin = moderator.role == "ADMIN"
    # Convert canon CanonFieldReport → FieldReportSchema for the service layer
    field_reports = [
        FieldReportSchema(
            field_path=fr.field_name,
            message=fr.comment,
        )
        for fr in body.field_reports
    ]
    ticket = await ModerationQueueService.block(
        session,
        ticket_id=ticket.id,
        moderator_id=moderator.id,
        blocking_reason_ids=[body.blocking_reason_id],
        comment=body.moderator_comment,
        field_reports=field_reports,
        is_admin=is_admin,
    )
    return TicketResponse.model_validate(ticket)
