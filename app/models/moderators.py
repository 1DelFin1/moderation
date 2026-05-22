from datetime import datetime
from uuid import uuid4, UUID

from sqlalchemy import String, Boolean, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin


class ModeratorModel(Base, TimestampMixin):
    __tablename__ = "moderators"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), default=uuid4, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="MODERATOR")
    category_specializations: Mapped[list[UUID] | None] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=True, default=list
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_moderators_email", "email"),
    )
