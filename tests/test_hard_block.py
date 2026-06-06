"""Tests for US-MOD-05: жёсткая блокировка (необратимая).

DoD scenarios (moderation-flows.md#hard-block):
  happy:
    - hard_block_transitions_to_terminal_and_emits_event
    - hard_block_event_carries_hard_block_true
  unhappy:
    - any_modify_on_hard_blocked_returns_403
    - edited_event_on_hard_blocked_is_ignored
    - deleted_event_removes_hard_blocked

ADR (гарантии необратимости HARD_BLOCKED):
  Рассмотрено три подхода:
  1. Терминальный статус с проверкой на каждом мутирующем endpoint (approve, block, release):
     guard `if ticket.status == "HARD_BLOCKED": raise 403` — защита в каждом методе сервиса.
     Риск: новый endpoint без guard'а открывает обходной путь. Аудит: grep на "HARD_BLOCKED".
     Data-fix через Django Admin: UPDATE tickets SET status='PENDING' WHERE id=... — прямой SQL.
  2. Отдельный флаг is_terminal в схеме: bool-флаг рядом со статусом, guard проверяет флаг.
     Плюс: явно читается в БД. Минус: два поля должны быть синхронны — риск рассинхрона.
     Сложность аудита выше (нужно проверять оба поля).
  3. Вынести HARD_BLOCKED в отдельную таблицу-архив: терминальный тикет переносится в
     hard_blocked_tickets, из tickets удаляется — физическая невозможность мутации. Плюс:
     самая строгая защита. Минус: дорогая миграция, JOIN для истории.

  Выбор: подход 1 (статус + guard).
  Критерии: (а) риск случайной правки — минимален при единственной точке входа через сервис;
  (б) сложность аудита — grep HARD_BLOCKED по сервису достаточен, новый метод заметен при ревью;
  data-fix через Django Admin прост (прямое UPDATE) и не требует переноса записей.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.moderation import ProductBlockingReasonModel, TicketModel
from app.services.moderation_queue_service import ModerationQueueService
from app.core.config import settings
from tests.conftest import TEST_MODERATOR_ID, make_mock_moderator

_SERVICE_KEY = settings.SERVICE_KEY

_PRODUCT_ID = uuid4()
_TICKET_ID = uuid4()
_REASON_ID = uuid4()
_NOW = datetime.now(timezone.utc)

_SKU = {
    "id": str(uuid4()),
    "name": "iPhone 15 Pro",
    "price": 12999000,
    "discount": 0,
    "image": "/s3/iphone.jpg",
    "active_quantity": 5,
    "characteristics": [],
}

_BODY = {
    "blocking_reason_id": str(_REASON_ID),
    "moderator_comment": "Товар является контрафактом",
    "field_reports": [],
}


def _make_ticket(status: str = "IN_REVIEW") -> TicketModel:
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
    t.json_after = {"skus": [_SKU], "title": "Test product"}
    t.json_before = None
    t.decision_comment = None
    t.blocking_reason_ids = None
    t.field_reports = []
    return t


def _make_hard_reason() -> ProductBlockingReasonModel:
    r = MagicMock(spec=ProductBlockingReasonModel)
    r.id = _REASON_ID
    r.hard_block = True
    r.title = "Контрафактный товар"
    return r


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hard_block_transitions_to_terminal_and_emits_event(mod_client):
    """POST /decline with hard_block reason → status HARD_BLOCKED, event sent to B2B.

    Happy path: moderator declines IN_REVIEW ticket using a hard_block reason.
    Response status must be HARD_BLOCKED (terminal, not reversible in normal flow).
    """
    hard_blocked_ticket = _make_ticket(status="HARD_BLOCKED")
    mock_block = AsyncMock(return_value=hard_blocked_ticket)

    with patch(
        "app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
        AsyncMock(return_value=_make_ticket(status="IN_REVIEW")),
    ), patch(
        "app.api.routers.product_moderation.ModerationQueueService.block",
        mock_block,
    ):
        resp = await mod_client.post(
            f"/api/v1/products/{_PRODUCT_ID}/decline",
            json=_BODY,
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "HARD_BLOCKED"


@pytest.mark.asyncio
async def test_hard_block_event_carries_hard_block_true():
    """ModerationQueueService.block() sends BLOCKED + hard_block=True to B2B.

    Service-layer test: verifies the outgoing event payload for a hard-block reason.
    """
    ticket = _make_ticket(status="IN_REVIEW")
    ticket.assigned_moderator_id = TEST_MODERATOR_ID

    hard_reason = _make_hard_reason()

    scalars_result = MagicMock()
    scalars_result.all = MagicMock(return_value=[hard_reason])

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.scalars = AsyncMock(return_value=scalars_result)
    # After commit, reload returns HARD_BLOCKED ticket
    hard_blocked_ticket = _make_ticket(status="HARD_BLOCKED")
    mock_session.scalar = AsyncMock(return_value=hard_blocked_ticket)

    mock_send = AsyncMock()

    with patch(
        "app.services.moderation_queue_service.ModerationQueueService.get_by_id",
        AsyncMock(return_value=ticket),
    ), patch("app.services.event_service.send_result_to_b2b", mock_send):
        result = await ModerationQueueService.block(
            mock_session,
            ticket_id=_TICKET_ID,
            moderator_id=TEST_MODERATOR_ID,
            blocking_reason_ids=[_REASON_ID],
            comment="Контрафакт подтверждён",
        )

    mock_send.assert_awaited_once()
    send_kwargs = mock_send.call_args.kwargs
    assert send_kwargs["status"] == "BLOCKED"
    assert send_kwargs["hard_block"] is True
    assert send_kwargs["blocking_reason"]["id"] == str(_REASON_ID)
    assert send_kwargs["blocking_reason"]["title"] == "Контрафактный товар"

    # Ticket status set to HARD_BLOCKED before commit
    assert ticket.status == "HARD_BLOCKED"


# ── Unhappy paths ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_any_modify_on_hard_blocked_returns_403_approve(mod_client):
    """approve on HARD_BLOCKED ticket returns 403 Forbidden.

    HARD_BLOCKED is terminal — no approve is allowed.
    """
    hard_blocked_ticket = _make_ticket(status="HARD_BLOCKED")

    with patch(
        "app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
        AsyncMock(return_value=hard_blocked_ticket),
    ), patch(
        "app.api.routers.product_moderation.ModerationQueueService.approve",
        AsyncMock(
            side_effect=HTTPException(
                status_code=403,
                detail={
                    "code": "FORBIDDEN",
                    "message": "Product is permanently blocked, no modifications allowed",
                },
            )
        ),
    ):
        resp = await mod_client.post(f"/api/v1/products/{_PRODUCT_ID}/approve")

    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_any_modify_on_hard_blocked_returns_403_decline(mod_client):
    """decline on HARD_BLOCKED ticket returns 403 Forbidden.

    HARD_BLOCKED is terminal — further blocking attempts are forbidden.
    """
    with patch(
        "app.api.routers.product_moderation.ModerationQueueService.get_by_product_id",
        AsyncMock(return_value=_make_ticket(status="HARD_BLOCKED")),
    ), patch(
        "app.api.routers.product_moderation.ModerationQueueService.block",
        AsyncMock(
            side_effect=HTTPException(
                status_code=403,
                detail={
                    "code": "FORBIDDEN",
                    "message": "Product is permanently blocked, no modifications allowed",
                },
            )
        ),
    ):
        resp = await mod_client.post(
            f"/api/v1/products/{_PRODUCT_ID}/decline",
            json=_BODY,
        )

    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_any_modify_on_hard_blocked_service_approve():
    """Service raises 403 for approve on HARD_BLOCKED ticket."""
    ticket = _make_ticket(status="HARD_BLOCKED")

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

    assert exc_info.value.status_code == 403
    assert "permanently blocked" in exc_info.value.detail["message"].lower()


@pytest.mark.asyncio
async def test_any_modify_on_hard_blocked_service_block():
    """Service raises 403 for block (decline) on HARD_BLOCKED ticket."""
    ticket = _make_ticket(status="HARD_BLOCKED")

    mock_session = AsyncMock(spec=AsyncSession)

    with patch(
        "app.services.moderation_queue_service.ModerationQueueService.get_by_id",
        AsyncMock(return_value=ticket),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await ModerationQueueService.block(
                mock_session,
                ticket_id=_TICKET_ID,
                moderator_id=TEST_MODERATOR_ID,
                blocking_reason_ids=[_REASON_ID],
            )

    assert exc_info.value.status_code == 403
    assert "permanently blocked" in exc_info.value.detail["message"].lower()


@pytest.mark.asyncio
async def test_edited_event_on_hard_blocked_is_ignored(ac):
    """EDITED event from B2B is silently ignored when product is HARD_BLOCKED.

    Canon MOD-1: if existing record has status=HARD_BLOCKED → ignore EDITED (200 OK).
    No new ticket is created; the product remains in its terminal state.
    """
    with patch(
        "app.api.routers.b2b_events._fetch_product_from_b2b",
        AsyncMock(return_value={"skus": [_SKU], "title": "Test"}),
    ), patch(
        "app.api.routers.b2b_events.ModerationQueueService.handle_b2b_event",
        AsyncMock(return_value=False),  # not duplicate, but skipped due to HARD_BLOCKED
    ):
        resp = await ac.post(
            "/api/v1/events/product",
            json={
                "product_id": str(_PRODUCT_ID),
                "seller_id": str(uuid4()),
                "event": "EDITED",
                "date": _NOW.isoformat(),
            },
            headers={"X-Service-Key": _SERVICE_KEY},
        )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_edited_event_on_hard_blocked_is_ignored_service_level():
    """Service skips PRODUCT_EDITED for HARD_BLOCKED — no new ticket created.

    Returns False (not duplicate) but does not insert a new ticket row.
    """
    product_id = _PRODUCT_ID

    hard_blocked_ticket = _make_ticket(status="HARD_BLOCKED")

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    # idempotency check: no existing ticket with this key
    # HARD_BLOCKED check: returns the hard-blocked ticket
    mock_session.scalar = AsyncMock(side_effect=[None, hard_blocked_ticket])

    result = await ModerationQueueService.handle_b2b_event(
        session=mock_session,
        event_type="PRODUCT_EDITED",
        idempotency_key=uuid4(),
        payload={
            "product_id": str(product_id),
            "seller_id": str(uuid4()),
            "json_after": {"skus": [_SKU]},
        },
    )

    # Not a duplicate, but skipped
    assert result is False
    # No new ticket was committed (commit not called after skipping)
    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_deleted_event_removes_hard_blocked():
    """PRODUCT_DELETED event cancels HARD_BLOCKED ticket.

    Canon: DELETED removes the record even if status is HARD_BLOCKED.
    Product remains blocked in B2B; Moderation drops its record.
    """
    product_id = _PRODUCT_ID
    hard_blocked_ticket = _make_ticket(status="HARD_BLOCKED")

    scalars_result = MagicMock()
    scalars_result.all = MagicMock(return_value=[hard_blocked_ticket])

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    # idempotency check → no existing, then scalars for cancellable tickets
    mock_session.scalar = AsyncMock(side_effect=[None, None])  # idem check + sentinel
    mock_session.scalars = AsyncMock(return_value=scalars_result)

    await ModerationQueueService.handle_b2b_event(
        session=mock_session,
        event_type="PRODUCT_DELETED",
        idempotency_key=uuid4(),
        payload={"product_id": str(product_id)},
    )

    # HARD_BLOCKED ticket was set to CANCELLED
    assert hard_blocked_ticket.status == "CANCELLED"
    # Changes were persisted
    mock_session.commit.assert_awaited_once()
