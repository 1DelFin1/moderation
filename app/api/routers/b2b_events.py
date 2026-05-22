from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import SessionDep, ServiceKeyDep
from app.schemas import IncomingB2BEvent
from app.services.moderation_queue_service import ModerationQueueService

router = APIRouter(prefix="/api/v1/b2b", tags=["b2b-events"])


@router.post("/events")
async def receive_b2b_event(
    body: IncomingB2BEvent,
    _: ServiceKeyDep,
    session: SessionDep,
):
    is_duplicate = await ModerationQueueService.handle_b2b_event(
        session=session,
        event_type=body.event_type,
        idempotency_key=body.idempotency_key,
        payload=body.payload,
    )
    if is_duplicate:
        raise HTTPException(
            status_code=409,
            detail={"code": "DUPLICATE_EVENT", "message": "Duplicate idempotency_key"},
        )
    return Response(status_code=status.HTTP_202_ACCEPTED)
