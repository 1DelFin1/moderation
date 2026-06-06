"""Tests for US-MOD-01 fix: EDITED updates in-place, CREATED blocks duplicates.

DoD scenarios:
  happy:
    - edited_upserts_pending_ticket        — EDITED на PENDING → обновляет, не создаёт новую запись
    - edited_on_blocked_sets_priority_2    — EDITED на BLOCKED → queue_priority=2
    - edited_on_moderated_with_stock_p3    — EDITED на MODERATED+qty>0 → priority=3
    - edited_on_moderated_no_stock_p4      — EDITED на MODERATED+qty=0 → priority=4
    - edited_resets_assignment             — EDITED сбрасывает assigned_moderator_id
    - edited_sets_json_before              — json_before = старый json_after
    - created_duplicate_product_ignored    — повторный CREATED для того же product_id → игнор (False)
  unhappy:
    - edited_unknown_product_returns_false — EDITED для несуществующего товара → False (200 OK)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4, UUID

import pytest

from app.models.moderation import TicketModel
from app.services.moderation_queue_service import ModerationQueueService, _compute_edited_priority
from tests.conftest import TEST_MODERATOR_ID

_PRODUCT_ID = uuid4()
_TICKET_ID = uuid4()
_IDEM_KEY = uuid4()
_NOW = datetime.now(timezone.utc)

_OLD_JSON_AFTER = {"title": "Old title", "skus": [{"active_quantity": 3}]}
_NEW_JSON_AFTER = {"title": "New title", "skus": [{"active_quantity": 5}]}


def _make_ticket(status: str = "PENDING", priority: int = 1) -> TicketModel:
    t = MagicMock(spec=TicketModel)
    t.id = _TICKET_ID
    t.product_id = _PRODUCT_ID
    t.seller_id = uuid4()
    t.category_id = None
    t.kind = "CREATE"
    t.status = status
    t.queue_priority = priority
    t.assigned_moderator_id = TEST_MODERATOR_ID
    t.claimed_at = _NOW
    t.claim_expires_at = _NOW + timedelta(minutes=30)
    t.json_after = _OLD_JSON_AFTER
    t.json_before = None
    t.field_reports = []
    t.blocking_reason_ids = [uuid4()] if status == "BLOCKED" else None
    t.idempotency_key = uuid4()
    t.created_at = _NOW
    t.updated_at = _NOW
    t.history = []
    return t


def _make_session(idem_hit=None, active_ticket=None):
    """Build a mock AsyncSession.

    idem_hit: TicketModel returned by the idempotency check (first scalar call).
    active_ticket: TicketModel returned by the active-ticket lookup (second scalar call).
    """
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    # scalar is called twice: once for idem key, once for active ticket
    session.scalar = AsyncMock(side_effect=[idem_hit, active_ticket])
    return session


# ── _compute_edited_priority unit tests ──────────────────────────────────────


def test_priority_blocked_always_2():
    assert _compute_edited_priority("BLOCKED", 1, {}) == 2


def test_priority_moderated_with_stock_is_3():
    json_after = {"skus": [{"active_quantity": 1}, {"active_quantity": 0}]}
    assert _compute_edited_priority("MODERATED", 1, json_after) == 3


def test_priority_moderated_no_stock_is_4():
    json_after = {"skus": [{"active_quantity": 0}]}
    assert _compute_edited_priority("MODERATED", 1, json_after) == 4


def test_priority_moderated_no_skus_is_4():
    assert _compute_edited_priority("MODERATED", 1, {}) == 4


def test_priority_pending_keeps_current():
    assert _compute_edited_priority("PENDING", 3, {}) == 3


def test_priority_in_review_keeps_current():
    assert _compute_edited_priority("IN_REVIEW", 2, {}) == 2


# ── Service-level integration tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_edited_upserts_pending_ticket():
    """EDITED on an existing PENDING ticket updates it in-place, no new row created."""
    ticket = _make_ticket(status="PENDING", priority=1)
    session = _make_session(idem_hit=None, active_ticket=ticket)

    await ModerationQueueService.handle_b2b_event(
        session=session,
        event_type="PRODUCT_EDITED",
        idempotency_key=_IDEM_KEY,
        payload={"product_id": str(_PRODUCT_ID), "seller_id": str(uuid4()), "json_after": _NEW_JSON_AFTER},
    )

    # json_before set to old json_after, json_after updated
    assert ticket.json_before == _OLD_JSON_AFTER
    assert ticket.json_after == _NEW_JSON_AFTER
    assert ticket.status == "PENDING"
    assert ticket.kind == "EDIT"
    assert ticket.idempotency_key == _IDEM_KEY
    assert ticket.assigned_moderator_id is None
    assert ticket.field_reports == []
    assert ticket.blocking_reason_ids is None

    # session.add called for ticket and history entry
    assert session.add.call_count == 2
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_edited_on_blocked_sets_priority_2():
    """EDITED on BLOCKED ticket → queue_priority=2 (seller fixed, re-review)."""
    ticket = _make_ticket(status="BLOCKED", priority=2)
    session = _make_session(idem_hit=None, active_ticket=ticket)

    await ModerationQueueService.handle_b2b_event(
        session=session,
        event_type="PRODUCT_EDITED",
        idempotency_key=_IDEM_KEY,
        payload={"product_id": str(_PRODUCT_ID), "seller_id": str(uuid4()), "json_after": _NEW_JSON_AFTER},
    )

    assert ticket.queue_priority == 2


@pytest.mark.asyncio
async def test_edited_on_moderated_with_stock_priority_3():
    """EDITED on MODERATED ticket with active SKUs → queue_priority=3."""
    ticket = _make_ticket(status="MODERATED", priority=1)
    json_after = {"skus": [{"active_quantity": 2}]}
    session = _make_session(idem_hit=None, active_ticket=ticket)

    await ModerationQueueService.handle_b2b_event(
        session=session,
        event_type="PRODUCT_EDITED",
        idempotency_key=_IDEM_KEY,
        payload={"product_id": str(_PRODUCT_ID), "seller_id": str(uuid4()), "json_after": json_after},
    )

    assert ticket.queue_priority == 3


@pytest.mark.asyncio
async def test_edited_on_moderated_no_stock_priority_4():
    """EDITED on MODERATED ticket with no active SKUs → queue_priority=4."""
    ticket = _make_ticket(status="MODERATED", priority=1)
    json_after = {"skus": [{"active_quantity": 0}]}
    session = _make_session(idem_hit=None, active_ticket=ticket)

    await ModerationQueueService.handle_b2b_event(
        session=session,
        event_type="PRODUCT_EDITED",
        idempotency_key=_IDEM_KEY,
        payload={"product_id": str(_PRODUCT_ID), "seller_id": str(uuid4()), "json_after": json_after},
    )

    assert ticket.queue_priority == 4


@pytest.mark.asyncio
async def test_edited_resets_assignment():
    """EDITED clears moderator assignment so the ticket goes back to the general queue."""
    ticket = _make_ticket(status="IN_REVIEW", priority=2)
    ticket.assigned_moderator_id = TEST_MODERATOR_ID
    session = _make_session(idem_hit=None, active_ticket=ticket)

    await ModerationQueueService.handle_b2b_event(
        session=session,
        event_type="PRODUCT_EDITED",
        idempotency_key=_IDEM_KEY,
        payload={"product_id": str(_PRODUCT_ID), "seller_id": str(uuid4()), "json_after": _NEW_JSON_AFTER},
    )

    assert ticket.assigned_moderator_id is None
    assert ticket.claimed_at is None
    assert ticket.claim_expires_at is None


@pytest.mark.asyncio
async def test_edited_unknown_product_returns_false():
    """EDITED for a product with no active ticket returns False (200 OK, no-op)."""
    session = _make_session(idem_hit=None, active_ticket=None)

    result = await ModerationQueueService.handle_b2b_event(
        session=session,
        event_type="PRODUCT_EDITED",
        idempotency_key=_IDEM_KEY,
        payload={"product_id": str(_PRODUCT_ID), "seller_id": str(uuid4()), "json_after": _NEW_JSON_AFTER},
    )

    assert result is False
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_created_duplicate_product_ignored():
    """Second CREATED for the same product_id (non-HARD_BLOCKED) returns False (no new ticket)."""
    existing = _make_ticket(status="PENDING", priority=1)
    session = _make_session(idem_hit=None, active_ticket=existing)

    result = await ModerationQueueService.handle_b2b_event(
        session=session,
        event_type="PRODUCT_CREATED",
        idempotency_key=_IDEM_KEY,
        payload={"product_id": str(_PRODUCT_ID), "seller_id": str(uuid4()), "json_after": _NEW_JSON_AFTER},
    )

    assert result is False
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_created_hard_blocked_product_ignored():
    """CREATED for a HARD_BLOCKED product is silently ignored."""
    existing = _make_ticket(status="HARD_BLOCKED", priority=1)
    session = _make_session(idem_hit=None, active_ticket=existing)

    result = await ModerationQueueService.handle_b2b_event(
        session=session,
        event_type="PRODUCT_CREATED",
        idempotency_key=_IDEM_KEY,
        payload={"product_id": str(_PRODUCT_ID), "seller_id": str(uuid4()), "json_after": _NEW_JSON_AFTER},
    )

    assert result is False
    session.add.assert_not_called()
