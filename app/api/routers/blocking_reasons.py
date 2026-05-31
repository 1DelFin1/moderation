from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep, ModeratorDep, AdminModeratorDep
from app.schemas import BlockingReasonCreate, BlockingReasonUpdate, BlockingReasonResponse
from app.services.moderation_queue_service import ModerationQueueService

router = APIRouter(
    prefix="/api/v1/product-blocking-reasons",
    tags=["blocking-reasons"],
)


@router.get("", response_model=list[BlockingReasonResponse])
async def list_blocking_reasons(
    moderator: ModeratorDep,
    session: SessionDep,
    hard_block: bool | None = None,
    is_active: Annotated[bool, Query()] = True,
):
    return await ModerationQueueService.get_blocking_reasons(session, hard_block=hard_block, is_active=is_active)


@router.post("", response_model=BlockingReasonResponse, status_code=status.HTTP_201_CREATED)
async def create_blocking_reason(
    body: BlockingReasonCreate,
    admin: AdminModeratorDep,
    session: SessionDep,
):
    return await ModerationQueueService.create_blocking_reason(session, body)


@router.patch("/{reason_id}", response_model=BlockingReasonResponse)
async def update_blocking_reason(
    reason_id: UUID,
    body: BlockingReasonUpdate,
    admin: AdminModeratorDep,
    session: SessionDep,
):
    return await ModerationQueueService.update_blocking_reason(session, reason_id, body)


@router.delete("/{reason_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_blocking_reason(
    reason_id: UUID,
    admin: AdminModeratorDep,
    session: SessionDep,
):
    await ModerationQueueService.delete_blocking_reason(session, reason_id)
