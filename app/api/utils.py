import secrets
from datetime import timedelta, datetime, timezone
from uuid import UUID

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import INCORRECT_DATA, INVALID_TOKEN
from app.core.security import verify_password
from app.core.config import settings
from app.models.refresh_tokens import RefreshTokenModel
from app.schemas import TokenResponse


class JWTAuthenticator:
    @staticmethod
    def create_access_token(payload: dict) -> str:
        to_encode = payload.copy()
        now = datetime.now(timezone.utc)
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode.update(exp=expire, iat=now)
        return jwt.encode(to_encode, settings.JWT_SECRET_KEY, settings.JWT_ALGORITHM)

    @staticmethod
    def decode_jwt_token(token: str) -> dict:
        return jwt.decode(token, settings.JWT_SECRET_KEY, [settings.JWT_ALGORITHM])


class RefreshTokenService:
    REFRESH_TOKEN_EXPIRE_DAYS = 7

    @classmethod
    async def create(cls, session: AsyncSession, account_id: str, account_type: str) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(days=cls.REFRESH_TOKEN_EXPIRE_DAYS)
        db_token = RefreshTokenModel(
            token=token,
            account_id=account_id,
            account_type=account_type,
            expires_at=expires_at,
        )
        session.add(db_token)
        await session.commit()
        return token

    @classmethod
    async def get_valid(cls, session: AsyncSession, token: str) -> RefreshTokenModel | None:
        stmt = select(RefreshTokenModel).where(
            RefreshTokenModel.token == token,
            RefreshTokenModel.revoked.is_(False),
            RefreshTokenModel.expires_at > datetime.now(timezone.utc),
        )
        return await session.scalar(stmt)

    @classmethod
    async def revoke(cls, session: AsyncSession, token: str) -> None:
        db_token = await session.scalar(
            select(RefreshTokenModel).where(RefreshTokenModel.token == token)
        )
        if db_token:
            db_token.revoked = True
            await session.commit()

    @classmethod
    async def revoke_all_for_account(cls, session: AsyncSession, account_id: str) -> None:
        from sqlalchemy import update
        await session.execute(
            update(RefreshTokenModel)
            .where(
                RefreshTokenModel.account_id == account_id,
                RefreshTokenModel.revoked.is_(False),
            )
            .values(revoked=True)
        )
        await session.commit()


class Authorization:
    @staticmethod
    async def login(session: AsyncSession, email: str, password: str) -> TokenResponse:
        from app.services.moderator_service import ModeratorService

        moderator = await ModeratorService.get_by_email(session, email)
        if moderator is None or not verify_password(password, moderator.hashed_password):
            raise INCORRECT_DATA

        if not moderator.is_active:
            raise INCORRECT_DATA

        access_payload = {
            "sub": str(moderator.id),
            "email": moderator.email,
            "account_type": "moderator",
        }
        access_token = JWTAuthenticator.create_access_token(access_payload)
        refresh_token = await RefreshTokenService.create(session, str(moderator.id), "moderator")

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user_id=UUID(str(moderator.id)),
            role=moderator.role,
        )

    @staticmethod
    async def refresh(session: AsyncSession, refresh_token: str) -> TokenResponse:
        from app.services.moderator_service import ModeratorService

        db_token = await RefreshTokenService.get_valid(session, refresh_token)
        if not db_token:
            raise INVALID_TOKEN

        await RefreshTokenService.revoke(session, refresh_token)
        new_refresh = await RefreshTokenService.create(session, db_token.account_id, db_token.account_type)

        access_payload = {
            "sub": db_token.account_id,
            "account_type": db_token.account_type,
        }
        access_token = JWTAuthenticator.create_access_token(access_payload)

        moderator = await ModeratorService.get_by_id(session, UUID(db_token.account_id))
        role = moderator.role if moderator else None

        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user_id=UUID(db_token.account_id),
            role=role,
        )
