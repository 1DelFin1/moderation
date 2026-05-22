from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.exceptions import MODERATOR_ALREADY_EXISTS, MODERATOR_NOT_FOUND
from app.models.moderators import ModeratorModel
from app.schemas import ModeratorCreate, ModeratorUpdate, ModeratorSelfUpdate


class ModeratorService:

    @classmethod
    async def get_by_id(cls, session: AsyncSession, moderator_id: UUID) -> ModeratorModel | None:
        stmt = select(ModeratorModel).where(ModeratorModel.id == moderator_id)
        return await session.scalar(stmt)

    @classmethod
    async def get_by_email(cls, session: AsyncSession, email: str) -> ModeratorModel | None:
        stmt = select(ModeratorModel).where(ModeratorModel.email == email)
        return await session.scalar(stmt)

    @classmethod
    async def create(cls, session: AsyncSession, data: ModeratorCreate) -> ModeratorModel:
        existing = await cls.get_by_email(session, data.email)
        if existing is not None:
            raise MODERATOR_ALREADY_EXISTS

        moderator = ModeratorModel(
            email=data.email,
            first_name=data.first_name,
            last_name=data.last_name,
            hashed_password=get_password_hash(data.password),
            is_active=True,
            role=data.role.value,
            category_specializations=data.category_specializations or [],
        )
        session.add(moderator)
        await session.commit()
        await session.refresh(moderator)
        return moderator

    @classmethod
    async def update(
        cls,
        session: AsyncSession,
        moderator: ModeratorModel,
        data: ModeratorUpdate,
    ) -> ModeratorModel:
        if data.first_name is not None:
            moderator.first_name = data.first_name
        if data.last_name is not None:
            moderator.last_name = data.last_name
        if data.role is not None:
            moderator.role = data.role.value
        if data.is_active is not None:
            moderator.is_active = data.is_active
        if data.category_specializations is not None:
            moderator.category_specializations = data.category_specializations

        session.add(moderator)
        await session.commit()
        await session.refresh(moderator)
        return moderator

    @classmethod
    async def self_update(
        cls,
        session: AsyncSession,
        moderator: ModeratorModel,
        data: ModeratorSelfUpdate,
    ) -> ModeratorModel:
        if data.first_name is not None:
            moderator.first_name = data.first_name
        if data.last_name is not None:
            moderator.last_name = data.last_name

        session.add(moderator)
        await session.commit()
        await session.refresh(moderator)
        return moderator

    @classmethod
    async def deactivate(cls, session: AsyncSession, moderator: ModeratorModel) -> None:
        moderator.is_active = False
        session.add(moderator)
        await session.commit()

    @classmethod
    async def list_paginated(
        cls,
        session: AsyncSession,
        limit: int,
        offset: int,
        is_active: bool | None = None,
    ) -> tuple[list[ModeratorModel], int]:
        stmt = select(ModeratorModel)
        count_stmt = select(func.count(ModeratorModel.id))

        if is_active is not None:
            stmt = stmt.where(ModeratorModel.is_active == is_active)
            count_stmt = count_stmt.where(ModeratorModel.is_active == is_active)

        total = await session.scalar(count_stmt) or 0
        stmt = stmt.offset(offset).limit(limit)
        result = await session.scalars(stmt)
        return list(result.all()), total
