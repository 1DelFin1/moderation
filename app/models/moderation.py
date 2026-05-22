from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4, UUID

from sqlalchemy import String, Integer, Boolean, Text, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB, ARRAY

from app.core.database import Base

if TYPE_CHECKING:
    pass


class ProductBlockingReasonModel(Base):
    __tablename__ = "product_blocking_reasons"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), default=uuid4, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    hard_block: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TicketModel(Base):
    __tablename__ = "tickets"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), default=uuid4, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    seller_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    category_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # CREATE | EDIT
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    queue_priority: Mapped[int] = mapped_column(
        Integer,
        CheckConstraint("queue_priority >= 1 AND queue_priority <= 4", name="chk_ticket_queue_priority"),
        nullable=False,
        default=3,
    )
    assigned_moderator_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    json_before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    json_after: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_reports: Mapped[list] = mapped_column(JSONB, default=list, nullable=True)
    blocking_reason_ids: Mapped[list[UUID] | None] = mapped_column(ARRAY(PGUUID(as_uuid=True)), nullable=True)
    idempotency_key: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    history: Mapped[list["TicketHistoryModel"]] = relationship(
        "TicketHistoryModel",
        lazy="noload",
        cascade="all, delete-orphan",
        foreign_keys="TicketHistoryModel.ticket_id",
        order_by="TicketHistoryModel.at",
    )


class TicketHistoryModel(Base):
    __tablename__ = "ticket_history"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), default=uuid4, primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)  # CREATED, CLAIMED, RELEASED, APPROVED, BLOCKED, HARD_BLOCKED, AUTO_RETURNED
    moderator_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


# Keep for backwards compatibility / migration reference
class ProductModerationModel(Base):
    __tablename__ = "product_moderation"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), default=uuid4, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True, nullable=False)
    seller_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(25), nullable=False)
    queue_priority: Mapped[int] = mapped_column(
        Integer,
        CheckConstraint("queue_priority >= 1 AND queue_priority <= 4", name="chk_queue_priority"),
        nullable=False,
    )
    total_active_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    json_before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    json_after: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    blocking_reason_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    moderator_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    moderator_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_created: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_moderation: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    field_reports: Mapped[list["ProductModerationFieldReportModel"]] = relationship(
        "ProductModerationFieldReportModel",
        lazy="noload",
        cascade="all, delete-orphan",
        foreign_keys="ProductModerationFieldReportModel.product_moderation_id",
    )


class ProductModerationFieldReportModel(Base):
    __tablename__ = "product_moderation_field_report"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), default=uuid4, primary_key=True)
    product_moderation_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_moderation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sku_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    date_created: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
