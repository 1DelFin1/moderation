from typing import Annotated, AsyncGenerator
from uuid import UUID

from jwt.exceptions import InvalidTokenError
from fastapi import Depends, Request, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.utils import JWTAuthenticator
from app.core.config import settings
from app.core.database import async_session_factory
from app.exceptions import UNAUTHORIZED, INVALID_TOKEN, MODERATOR_NOT_FOUND, FORBIDDEN
from app.models.moderators import ModeratorModel
from app.services.moderator_service import ModeratorService


async def get_session() -> AsyncGenerator:
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _extract_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]
    token = request.cookies.get("token")
    if token:
        return token
    raise UNAUTHORIZED


async def get_current_active_auth_moderator(
    request: Request,
    session: SessionDep,
) -> ModeratorModel:
    token = _extract_token(request)

    try:
        payload = JWTAuthenticator.decode_jwt_token(token)
    except InvalidTokenError:
        raise INVALID_TOKEN

    account_type = payload.get("account_type")
    if account_type != "moderator":
        raise UNAUTHORIZED

    sub = payload.get("sub")
    email = payload.get("email")

    if not sub and not email:
        raise INVALID_TOKEN

    moderator = (
        await ModeratorService.get_by_email(session, email)
        if email
        else await ModeratorService.get_by_id(session, UUID(sub))
    )
    if moderator is None:
        raise MODERATOR_NOT_FOUND

    if not moderator.is_active:
        raise UNAUTHORIZED

    return moderator


async def get_current_active_auth_admin_moderator(
    request: Request,
    session: SessionDep,
) -> ModeratorModel:
    moderator = await get_current_active_auth_moderator(request, session)
    if moderator.role != "ADMIN":
        raise FORBIDDEN
    return moderator


async def verify_service_key(
    x_service_key: Annotated[str | None, Header(alias="X-Service-Key")] = None,
) -> None:
    if not x_service_key or x_service_key != settings.SERVICE_KEY:
        raise UNAUTHORIZED


def get_moderator_id(payload: dict) -> UUID:
    sub = payload.get("sub")
    if not sub:
        raise INVALID_TOKEN
    return UUID(sub)


ModeratorDep = Annotated[ModeratorModel, Depends(get_current_active_auth_moderator)]
AdminModeratorDep = Annotated[ModeratorModel, Depends(get_current_active_auth_admin_moderator)]
ServiceKeyDep = Annotated[None, Depends(verify_service_key)]
