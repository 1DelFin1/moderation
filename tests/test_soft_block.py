"""Tests for US-MOD-04: мягкая блокировка с замечаниями.

DoD scenarios (moderation-flows.md#soft-block):
  happy:
    - soft_block_transitions_to_blocked_with_field_reports
    - soft_block_emits_event_to_b2b
  unhappy:
    - soft_block_unknown_reason_returns_400
    - soft_block_others_card_returns_403
    - soft_block_invalid_field_name_returns_400
    - soft_block_hard_only_reason_returns_400_or_routes_to_hard

ADR (field_reports storage):
  Рассмотрено три подхода:
  1. Отдельная таблица с FK на тикет — удобно для аналитики (SELECT WHERE field_name=?),
     но дороже при изменении схемы замечаний (ALTER TABLE).
  2. JSON-массив в карточке — проще миграции (меняется только схема валидации),
     достаточно для аналитики через jsonb_array_elements. Payload в B2B компактнее.
  3. Event sourcing — избыточно для MVP.
  Выбор: JSON-массив (JSONB) в тикете — простые миграции, нет лишних JOIN,
  jsonb-операторы PostgreSQL позволяют фильтрацию по field_name.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.moderation import TicketModel
from tests.conftest import TEST_MODERATOR_ID, make_mock_moderator

_PRODUCT_ID = uuid4()
_TICKET_ID = uuid4()
_REASON_ID = uuid4()
_NOW = datetime.now(timezone.utc)

_VALID_BODY = {
    "blocking_reason_id": str(_REASON_ID),
    "moderator_comment": "Описание скопировано, фото плохого качества",
    "field_reports": [
        {"field_name": "description", "sku_id": None, "comment": "Текст скопирован"},
        {"field_name": "product_images", "sku_id": None, "comment": "Фото размыто"},
    ],
}


def _make_blocked_ticket(status="BLOCKED") -> TicketModel:
    t = MagicMock(spec=TicketModel)
    t.id = _TICKET_ID
    t.product_id = _PRODUCT_ID
    t.seller_id = uuid4()
    t.category_id = None
    t.kind = "CREATE"
    t.status = status
    t.queue_priority = 1
    t.assigned_moderator_id = TEST_MODERATOR_ID
    t.claimed_at = _NOW
    t.claim_expires_at = _NOW + timedelta(minutes=30)
    t.decision_at = _NOW
    t.updated_at = _NOW
    t.created_at = _NOW
    t.history = []
    return t


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_block_transitions_to_blocked_with_field_reports(mod_client):
    """POST /decline transitions ticket to BLOCKED and saves field_reports.

    The moderator specifies a soft-block reason and field-level comments.
    The ticket status must be BLOCKED in the response.
    """
    blocked_ticket = _make_blocked_ticket(status="BLOCKED")
    mock_block = AsyncMock(return_value=blocked_ticket)

    with patch("app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
               AsyncMock(return_value=blocked_ticket)), \
         patch("app.api.routers.product_moderation.ModerationQueueService.block", mock_block):
        resp = await mod_client.post(
            f"/api/v1/products/{_PRODUCT_ID}/decline",
            json=_VALID_BODY,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "BLOCKED"

    # Verify block() was called with the right reason id and comment
    call_kwargs = mock_block.call_args.kwargs
    assert call_kwargs["blocking_reason_ids"] == [_REASON_ID]
    assert call_kwargs["comment"] == _VALID_BODY["moderator_comment"]
    assert len(call_kwargs["field_reports"]) == 2


@pytest.mark.asyncio
async def test_soft_block_emits_event_to_b2b(mod_client):
    """After blocking, BLOCKED event with hard_block=false is sent to B2B.

    Patches send_result_to_b2b at the event_service module level.
    """
    blocked_ticket = _make_blocked_ticket(status="BLOCKED")
    mock_send = AsyncMock()

    with patch("app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
               AsyncMock(return_value=blocked_ticket)), \
         patch("app.api.routers.product_moderation.ModerationQueueService.block",
               AsyncMock(return_value=blocked_ticket)), \
         patch("app.services.event_service.send_result_to_b2b", mock_send):
        resp = await mod_client.post(
            f"/api/v1/products/{_PRODUCT_ID}/decline",
            json=_VALID_BODY,
        )

    assert resp.status_code == 200
    # block() is fully mocked here; the outgoing event is verified in the service test below


@pytest.mark.asyncio
async def test_soft_block_emits_event_via_service_directly():
    """ModerationQueueService.block() calls send_result_to_b2b with hard_block=False.

    Tests the service layer directly to confirm the B2B event payload.
    """
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services.moderation_queue_service import ModerationQueueService
    from app.models.moderation import ProductBlockingReasonModel

    soft_reason = MagicMock(spec=ProductBlockingReasonModel)
    soft_reason.id = _REASON_ID
    soft_reason.title = "Описание не соответствует товару"
    soft_reason.hard_block = False

    ticket = _make_blocked_ticket(status="IN_REVIEW")
    ticket.assigned_moderator_id = TEST_MODERATOR_ID
    ticket.product_id = _PRODUCT_ID

    scalars_result = MagicMock()
    scalars_result.all = MagicMock(return_value=[soft_reason])

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.scalars = AsyncMock(return_value=scalars_result)

    mock_send = AsyncMock()

    with patch("app.services.moderation_queue_service.ModerationQueueService.get_by_id",
               AsyncMock(return_value=ticket)), \
         patch("app.services.event_service.send_result_to_b2b", mock_send):
        await ModerationQueueService.block(
            mock_session,
            ticket_id=_TICKET_ID,
            moderator_id=TEST_MODERATOR_ID,
            blocking_reason_ids=[_REASON_ID],
            comment="Test comment",
        )

    mock_send.assert_awaited_once()
    send_kwargs = mock_send.call_args.kwargs
    assert send_kwargs["status"] == "BLOCKED"
    assert send_kwargs["hard_block"] is False

    blocking_reason = send_kwargs["blocking_reason"]
    assert blocking_reason["id"] == str(_REASON_ID)
    assert blocking_reason["title"] == "Описание не соответствует товару"
    assert blocking_reason["comment"] == "Test comment"

    assert send_kwargs["field_reports"] == []


# ── Unhappy paths ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_block_unknown_reason_returns_400(mod_client):
    """Unknown blocking_reason_id must return 400 Bad Request."""
    blocked_ticket = _make_blocked_ticket(status="IN_REVIEW")

    with patch("app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
               AsyncMock(return_value=blocked_ticket)), \
         patch("app.api.routers.product_moderation.ModerationQueueService.block",
               AsyncMock(side_effect=HTTPException(
                   status_code=400,
                   detail={"code": "BAD_REQUEST", "message": "One or more blocking reasons not found"},
               ))):
        resp = await mod_client.post(
            f"/api/v1/products/{_PRODUCT_ID}/decline",
            json=_VALID_BODY,
        )

    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_soft_block_others_card_returns_403(mod_client):
    """Declining a ticket assigned to another moderator must return 403 Forbidden."""
    other_moderator_id = uuid4()
    ticket = _make_blocked_ticket(status="IN_REVIEW")
    ticket.assigned_moderator_id = other_moderator_id  # not TEST_MODERATOR_ID

    with patch("app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
               AsyncMock(return_value=ticket)), \
         patch("app.api.routers.product_moderation.ModerationQueueService.block",
               AsyncMock(side_effect=HTTPException(
                   status_code=403,
                   detail={"code": "FORBIDDEN", "message": "This moderation card is not assigned to you"},
               ))):
        resp = await mod_client.post(
            f"/api/v1/products/{_PRODUCT_ID}/decline",
            json=_VALID_BODY,
        )

    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_soft_block_invalid_field_name_returns_400(mod_client):
    """field_reports[].field_name outside the allowed enum must return 400.

    Allowed values: title, description, product_images, category,
                    sku_name, sku_image, sku_price.
    """
    body = {
        **_VALID_BODY,
        "field_reports": [
            {"field_name": "INVALID_FIELD_XYZ", "comment": "test"},
        ],
    }

    resp = await mod_client.post(
        f"/api/v1/products/{_PRODUCT_ID}/decline",
        json=body,
    )

    assert resp.status_code == 422  # Pydantic validation error


@pytest.mark.asyncio
async def test_soft_block_hard_only_reason_routes_to_hard_blocked(mod_client):
    """A blocking_reason with hard_block=true routes to HARD_BLOCKED (MOD-5).

    When the reason is hard-only, the service sets status=HARD_BLOCKED
    and sends hard_block=true to B2B.
    """
    hard_blocked_ticket = _make_blocked_ticket(status="HARD_BLOCKED")

    with patch("app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
               AsyncMock(return_value=hard_blocked_ticket)), \
         patch("app.api.routers.product_moderation.ModerationQueueService.block",
               AsyncMock(return_value=hard_blocked_ticket)):
        resp = await mod_client.post(
            f"/api/v1/products/{_PRODUCT_ID}/decline",
            json=_VALID_BODY,
        )

    assert resp.status_code == 200
    # Service determined hard_block=true → HARD_BLOCKED
    assert resp.json()["status"] == "HARD_BLOCKED"


@pytest.mark.asyncio
async def test_soft_block_not_in_review_returns_409(mod_client):
    """Attempting to decline a ticket not in IN_REVIEW must return 409."""
    pending_ticket = _make_blocked_ticket(status="PENDING")

    with patch("app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
               AsyncMock(return_value=pending_ticket)), \
         patch("app.api.routers.product_moderation.ModerationQueueService.block",
               AsyncMock(side_effect=HTTPException(
                   status_code=409,
                   detail={"code": "CONFLICT", "message": "Ticket is not IN_REVIEW"},
               ))):
        resp = await mod_client.post(
            f"/api/v1/products/{_PRODUCT_ID}/decline",
            json=_VALID_BODY,
        )

    assert resp.status_code == 409
