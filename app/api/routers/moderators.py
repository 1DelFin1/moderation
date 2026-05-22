from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep, ModeratorDep, AdminModeratorDep
from app.exceptions import MODERATOR_NOT_FOUND
from app.schemas import (
    ModeratorResponse,
    ModeratorUpdate,
    ModeratorCreate,
    PaginatedModerators,
)
from app.services.moderator_service import ModeratorService

router = APIRouter(prefix="/api/v1/moderators", tags=["moderators"])


@router.get("", response_model=PaginatedModerators)
async def list_moderators(
    admin: AdminModeratorDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    is_active: Annotated[bool | None, Query()] = None,
):
    items, total = await ModeratorService.list_paginated(session, limit=limit, offset=offset, is_active=is_active)
    return PaginatedModerators(
        items=[ModeratorResponse.model_validate(m) for m in items],
        total_count=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ModeratorResponse, status_code=status.HTTP_201_CREATED)
async def create_moderator(
    body: ModeratorCreate,
    admin: AdminModeratorDep,
    session: SessionDep,
):
    return await ModeratorService.create(session, body)


@router.get("/me", response_model=ModeratorResponse)
async def get_me(moderator: ModeratorDep):
    return moderator


@router.get("/{moderator_id}", response_model=ModeratorResponse)
async def get_moderator(
    moderator_id: UUID,
    _moderator: ModeratorDep,
    session: SessionDep,
):
    mod = await ModeratorService.get_by_id(session, moderator_id)
    if mod is None:
        raise MODERATOR_NOT_FOUND
    return mod


@router.patch("/{moderator_id}", response_model=ModeratorResponse)
async def update_moderator(
    moderator_id: UUID,
    body: ModeratorUpdate,
    admin: AdminModeratorDep,
    session: SessionDep,
):
    mod = await ModeratorService.get_by_id(session, moderator_id)
    if mod is None:
        raise MODERATOR_NOT_FOUND
    return await ModeratorService.update(session, mod, body)


@router.delete("/{moderator_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_moderator(
    moderator_id: UUID,
    admin: AdminModeratorDep,
    session: SessionDep,
):
    mod = await ModeratorService.get_by_id(session, moderator_id)
    if mod is None:
        raise MODERATOR_NOT_FOUND
    await ModeratorService.deactivate(session, mod)
