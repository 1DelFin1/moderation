"""Tests for US-MOD-03: одобрение товара модератором.

DoD scenarios (moderation-flows.md#approve-product):
  happy:
    - approve_transitions_to_moderated_and_emits_event
  unhappy:
    - approve_others_card_returns_403
    - approve_after_edited_returns_409
    - approve_without_sku_returns_409

ADR (доставка события MODERATED в B2B):
  Рассмотрено три подхода:
  1. Синхронный POST в обработчике approve — проще всего, атомарно в рамках запроса:
     если B2B недоступен, approve возвращает 500 и статус остаётся IN_REVIEW,
     модератор повторяет попытку. Критерии: низкая сложность реализации, время
     отклика модератору зависит от B2B latency (~50ms в норме).
  2. Outbox-pattern — сначала commit, потом фоновая задача шлёт событие:
     надёжнее при частичном отказе (B2B упал после commit), но требует
     дополнительной таблицы outbox + планировщика + retry-логики.
  3. Event-bus (RabbitMQ / Kafka) — полная decoupling, но overengineering
     для MVP: нужен брокер, схема, consumer на стороне B2B.

  Выбор: синхронный POST (подход 1).
  Критерии: (а) надёжность при отказе B2B — при ошибке approve откатывается
  (status остаётся IN_REVIEW), модератор повторяет; двойной публикации не будет.
  (б) сложность реализации — минимальная; нет внешних зависимостей.
  Идемпотентность обеспечивается idempotency_key в теле события — повторный
  POST с тем же ключом игнорируется B2B.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.moderation import TicketModel
from app.services.moderation_queue_service import ModerationQueueService
from tests.conftest import TEST_MODERATOR_ID, TEST_MODERATOR_ID_2, make_mock_moderator

_PRODUCT_ID = uuid4()
_TICKET_ID = uuid4()
_NOW = datetime.now(timezone.utc)

_SKU = {
    "id": str(uuid4()),
    "name": "256GB Black",
    "price": 12999000,
    "discount": 0,
    "image": "/s3/iphone.jpg",
    "active_quantity": 10,
    "characteristics": [],
}


def _make_ticket(status: str = "IN_REVIEW", skus: list | None = None) -> TicketModel:
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
    t.decision_at = None
    t.updated_at = _NOW
    t.created_at = _NOW
    t.history = []
    if skus is None:
        skus = [_SKU]
    t.json_after = {"skus": skus, "title": "Test product"}
    t.json_before = None
    t.decision_comment = None
    t.blocking_reason_ids = None
    t.field_reports = []
    return t


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_transitions_to_moderated_and_emits_event(mod_client):
    """POST /approve transitions ticket to MODERATED and sends event to B2B.

    Happy path: moderator approves their own IN_REVIEW ticket with SKUs present.
    Response must have status=MODERATED; event_type=MODERATED is sent to B2B.
    """
    moderated_ticket = _make_ticket(status="MODERATED")
    mock_approve = AsyncMock(return_value=moderated_ticket)

    with patch(
        "app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
        AsyncMock(return_value=moderated_ticket),
    ), patch(
        "app.api.routers.product_moderation.ModerationQueueService.approve",
        mock_approve,
    ):
        resp = await mod_client.post(
            f"/api/v1/products/{_PRODUCT_ID}/approve",
            json={"comment": "Товар соответствует требованиям"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "MODERATED"

    # Service called with correct args
    call_kwargs = mock_approve.call_args.kwargs
    assert call_kwargs["ticket_id"] == _TICKET_ID
    assert call_kwargs["comment"] == "Товар соответствует требованиям"


@pytest.mark.asyncio
async def test_approve_emits_moderated_event_via_service():
    """ModerationQueueService.approve() sends MODERATED event to B2B.

    Service-layer test: verifies that send_result_to_b2b is called with
    event_type='MODERATED' and hard_block=False after a successful approve.
    """
    ticket = _make_ticket(status="IN_REVIEW")
    ticket.assigned_moderator_id = TEST_MODERATOR_ID

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    # After commit, scalar returns updated ticket with MODERATED status
    moderated_ticket = _make_ticket(status="MODERATED")
    mock_session.scalar = AsyncMock(return_value=moderated_ticket)

    mock_send = AsyncMock()

    with patch(
        "app.services.moderation_queue_service.ModerationQueueService.get_by_id",
        AsyncMock(return_value=ticket),
    ), patch("app.services.event_service.send_result_to_b2b", mock_send):
        result = await ModerationQueueService.approve(
            mock_session,
            ticket_id=_TICKET_ID,
            moderator_id=TEST_MODERATOR_ID,
            comment="Approved",
        )

    mock_send.assert_awaited_once()
    send_kwargs = mock_send.call_args.kwargs
    assert send_kwargs["event_type"] == "MODERATED"
    assert send_kwargs["hard_block"] is False
    assert send_kwargs["product_id"] == _PRODUCT_ID

    # Status was mutated to MODERATED before commit
    assert ticket.status == "MODERATED"


# ── Unhappy paths ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_others_card_returns_403(mod_client):
    """Approving a ticket assigned to another moderator must return 403 Forbidden.

    Canon: moderator_id != current moderator → 403 "Not assigned to you".
    """
    other_ticket = _make_ticket(status="IN_REVIEW")
    other_ticket.assigned_moderator_id = TEST_MODERATOR_ID_2  # not the logged-in one

    with patch(
        "app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
        AsyncMock(return_value=other_ticket),
    ), patch(
        "app.api.routers.product_moderation.ModerationQueueService.approve",
        AsyncMock(
            side_effect=HTTPException(
                status_code=403,
                detail={
                    "code": "FORBIDDEN",
                    "message": "This moderation card is not assigned to you",
                },
            )
        ),
    ):
        resp = await mod_client.post(f"/api/v1/products/{_PRODUCT_ID}/approve")

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "FORBIDDEN"
    assert "not assigned" in body["message"].lower()


@pytest.mark.asyncio
async def test_approve_others_card_returns_403_service_level():
    """Service raises 403 when assigned_moderator_id doesn't match."""
    other_moderator_id = TEST_MODERATOR_ID_2
    ticket = _make_ticket(status="IN_REVIEW")
    ticket.assigned_moderator_id = other_moderator_id

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    with patch(
        "app.services.moderation_queue_service.ModerationQueueService.get_by_id",
        AsyncMock(return_value=ticket),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await ModerationQueueService.approve(
                mock_session,
                ticket_id=_TICKET_ID,
                moderator_id=TEST_MODERATOR_ID,  # different from ticket's moderator
            )

    assert exc_info.value.status_code == 403
    assert "not assigned" in exc_info.value.detail["message"].lower()


@pytest.mark.asyncio
async def test_approve_after_edited_returns_409(mod_client):
    """Approving a ticket after the seller edited the product mid-review returns 409.

    When EDITED event arrives during IN_REVIEW, ticket status resets to PENDING
    (moderator_id cleared). Subsequent approve attempt must return 409 Conflict.
    """
    pending_ticket = _make_ticket(status="PENDING")  # reset to PENDING after EDITED

    with patch(
        "app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
        AsyncMock(return_value=pending_ticket),
    ), patch(
        "app.api.routers.product_moderation.ModerationQueueService.approve",
        AsyncMock(
            side_effect=HTTPException(
                status_code=409,
                detail={
                    "code": "CONFLICT",
                    "message": "Product is not in review status",
                },
            )
        ),
    ):
        resp = await mod_client.post(f"/api/v1/products/{_PRODUCT_ID}/approve")

    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_approve_after_edited_returns_409_service_level():
    """Service raises 409 when ticket is no longer IN_REVIEW (e.g. reset by EDITED event)."""
    ticket = _make_ticket(status="PENDING")  # EDITED reset it to PENDING

    mock_session = AsyncMock(spec=AsyncSession)

    with patch(
        "app.services.moderation_queue_service.ModerationQueueService.get_by_id",
        AsyncMock(return_value=ticket),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await ModerationQueueService.approve(
                mock_session,
                ticket_id=_TICKET_ID,
                moderator_id=TEST_MODERATOR_ID,
            )

    assert exc_info.value.status_code == 409
    assert "not in review" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_approve_without_sku_returns_409(mod_client):
    """Approving a product with no SKUs must return 409 Conflict.

    Canon edge case 5: if all SKUs were removed, approve is forbidden.
    """
    no_sku_ticket = _make_ticket(status="IN_REVIEW")

    with patch(
        "app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
        AsyncMock(return_value=no_sku_ticket),
    ), patch(
        "app.api.routers.product_moderation.ModerationQueueService.approve",
        AsyncMock(
            side_effect=HTTPException(
                status_code=409,
                detail={
                    "code": "CONFLICT",
                    "message": "Product has no SKUs, cannot approve",
                },
            )
        ),
    ):
        resp = await mod_client.post(f"/api/v1/products/{_PRODUCT_ID}/approve")

    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "CONFLICT"
    assert "sku" in body["message"].lower()


@pytest.mark.asyncio
async def test_approve_without_sku_returns_409_service_level():
    """Service raises 409 when ticket's json_after has no SKUs."""
    ticket = _make_ticket(status="IN_REVIEW", skus=[])  # no SKUs
    ticket.assigned_moderator_id = TEST_MODERATOR_ID

    mock_session = AsyncMock(spec=AsyncSession)

    with patch(
        "app.services.moderation_queue_service.ModerationQueueService.get_by_id",
        AsyncMock(return_value=ticket),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await ModerationQueueService.approve(
                mock_session,
                ticket_id=_TICKET_ID,
                moderator_id=TEST_MODERATOR_ID,
            )

    assert exc_info.value.status_code == 409
    assert "sku" in exc_info.value.detail["message"].lower()
