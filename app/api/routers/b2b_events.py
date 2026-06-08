"""POST /api/v1/b2b/events — incoming product events from B2B.

Canonical flow: moderation-flows.md#receive-product-events (MOD-1)

Accepts IncomingB2BEvent {event_type, idempotency_key, occurred_at, payload}
where payload already contains json_after/json_before — no B2B fetch needed.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, status

from app.api.deps import SessionDep, ServiceKeyDep
from app.schemas import IncomingB2BEvent
from app.services.moderation_queue_service import ModerationQueueService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/b2b", tags=["b2b-events"])


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def receive_product_event(
    body: IncomingB2BEvent,
    _: ServiceKeyDep,
    session: SessionDep,
):
    """Receive PRODUCT_CREATED / PRODUCT_EDITED / PRODUCT_DELETED event from B2B.

    Idempotent: repeated event with same idempotency_key returns 202 without side effects.
    """
    is_duplicate = await ModerationQueueService.handle_b2b_event(
        session=session,
        event_type=body.event_type,
        idempotency_key=body.idempotency_key,
        payload=body.payload,
    )

    if is_duplicate:
        logger.info(
            "Duplicate B2B event ignored: %s idempotency_key=%s",
            body.event_type,
            body.idempotency_key,
        )

    return {"accepted": True}
