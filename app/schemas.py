from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ---------------------------------------------------------------------------
# Auth schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    user_id: UUID
    role: str | None = None


# ---------------------------------------------------------------------------
# Moderator schemas
# ---------------------------------------------------------------------------

class ModeratorRole(str, Enum):
    MODERATOR = "MODERATOR"
    ADMIN = "ADMIN"


class ModeratorCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)
    first_name: str = Field(max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    role: ModeratorRole
    category_specializations: list[UUID] = []


class ModeratorUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    role: ModeratorRole | None = None
    is_active: bool | None = None
    category_specializations: list[UUID] | None = None


class ModeratorSelfUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None


class ModeratorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    first_name: str
    last_name: str | None
    role: ModeratorRole
    is_active: bool
    category_specializations: list[UUID] = []
    created_at: datetime
    last_login_at: datetime | None


class PaginatedModerators(BaseModel):
    items: list[ModeratorResponse]
    total_count: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Blocking reason schemas
# ---------------------------------------------------------------------------

class BlockingReasonCreate(BaseModel):
    code: str = Field(pattern=r'^[A-Z_]+$', max_length=64)
    title: str = Field(max_length=200)
    description: str | None = None
    hard_block: bool


class BlockingReasonUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    is_active: bool | None = None


class BlockingReasonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    title: str
    description: str | None
    hard_block: bool
    is_active: bool


# ---------------------------------------------------------------------------
# Ticket / queue schemas
# ---------------------------------------------------------------------------

class DiffEntry(BaseModel):
    field: str
    old_value: Any | None = None
    new_value: Any | None = None


class FieldReportSchema(BaseModel):
    field_path: str
    message: str
    severity: Literal["INFO", "WARNING", "ERROR"] = "ERROR"
    sku_id: UUID | None = None


class TicketHistoryEntrySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    at: datetime
    action: str
    moderator_id: UUID | None
    comment: str | None


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    seller_id: UUID
    category_id: UUID | None
    kind: str  # CREATE | EDIT
    status: str  # PENDING | IN_REVIEW | APPROVED | BLOCKED | HARD_BLOCKED
    queue_priority: int
    assigned_moderator_id: UUID | None
    claimed_at: datetime | None
    claim_expires_at: datetime | None
    decision_at: datetime | None
    created_at: datetime
    updated_at: datetime | None


class TicketDetailResponse(TicketResponse):
    json_before: dict | None
    json_after: dict
    field_reports: list[FieldReportSchema] = []
    blocking_reasons: list[BlockingReasonResponse] = []
    decision_comment: str | None
    history: list[TicketHistoryEntrySchema] = []
    diff: list[DiffEntry] | None = None


class PaginatedTickets(BaseModel):
    items: list[TicketResponse]
    total_count: int
    limit: int
    offset: int


class ClaimNextRequest(BaseModel):
    queue_priority: int | None = None
    category_ids: list[UUID] | None = None


class ApproveRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class BlockDecisionRequest(BaseModel):
    blocking_reason_ids: list[UUID] = Field(min_length=1)
    comment: str | None = Field(default=None, max_length=2000)
    field_reports: list[FieldReportSchema] = []


# Canon field_name enum (moderation-flows.md#soft-block)
FieldName = Literal[
    "title", "description", "product_images",
    "category", "sku_name", "sku_image", "sku_price",
]


class CanonFieldReport(BaseModel):
    field_name: FieldName
    sku_id: UUID | None = None
    comment: str = Field(max_length=500)


class DeclineRequest(BaseModel):
    """Canon request body for POST /api/v1/products/{product_id}/decline (MOD-4)."""
    blocking_reason_id: UUID
    moderator_comment: str = Field(max_length=1000)
    field_reports: list[CanonFieldReport] = []


# ---------------------------------------------------------------------------
# B2B event schemas
# ---------------------------------------------------------------------------

class EventPayloadCreated(BaseModel):
    product_id: UUID
    seller_id: UUID
    category_id: UUID | None = None
    queue_priority: int = 3
    json_after: dict


class EventPayloadEdited(BaseModel):
    product_id: UUID
    seller_id: UUID
    category_id: UUID | None = None
    queue_priority: int = 3
    json_before: dict
    json_after: dict


class EventPayloadDeleted(BaseModel):
    product_id: UUID


class IncomingB2BEvent(BaseModel):
    event_type: Literal["PRODUCT_CREATED", "PRODUCT_EDITED", "PRODUCT_DELETED"]
    idempotency_key: UUID
    occurred_at: datetime
    payload: dict  # parsed inside handler


class ProductEventFromB2B(BaseModel):
    """Canonical event format from B2B (moderation-flows.md MOD-1).

    POST /api/v1/events/product
    """
    product_id: UUID
    seller_id: UUID
    event: Literal["CREATED", "EDITED", "DELETED"]
    date: datetime


# ---------------------------------------------------------------------------
# Stats schemas
# ---------------------------------------------------------------------------

class StatsOverview(BaseModel):
    pending_count: int
    in_review_count: int
    approved_count: int
    blocked_count: int
    hard_blocked_count: int
    avg_review_time_seconds: int | None = None
    pending_by_priority: dict | None = None  # {"1": n, "2": n, "3": n, "4": n}


class ModeratorStats(BaseModel):
    moderator_id: UUID
    moderator_name: str | None
    decisions_count: int
    approved_count: int
    blocked_count: int
    hard_blocked_count: int
    avg_review_time_seconds: int | None = None
    released_count: int = 0
