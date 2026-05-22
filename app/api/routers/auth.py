from fastapi import APIRouter, Response, status

from app.api.deps import SessionDep, ModeratorDep
from app.api.utils import Authorization, RefreshTokenService
from app.exceptions import INVALID_TOKEN
from app.schemas import (
    LoginRequest,
    RefreshRequest,
    TokenResponse,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: SessionDep):
    return await Authorization.login(session, body.email, body.password)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, session: SessionDep):
    return await Authorization.refresh(session, body.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(moderator: ModeratorDep, session: SessionDep):
    await RefreshTokenService.revoke_all_for_account(session, str(moderator.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
