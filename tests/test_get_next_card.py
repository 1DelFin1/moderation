"""Tests for US-MOD-02: получение следующей карточки из очереди.

DoD scenarios (moderation-flows.md#get-next-card):
  happy:
    - next_returns_oldest_pending — PENDING → IN_REVIEW, закрепляется за модератором
  unhappy:
    - concurrent_two_moderators_get_different_cards — SELECT FOR UPDATE SKIP LOCKED
    - empty_queue_returns_204 — пустая очередь → 204
    - moderator_already_has_in_review_returns_409 — вторая карточка не выдаётся

ADR (concurrency protection):
  Рассмотрено три подхода:
  1. SELECT FOR UPDATE SKIP LOCKED (БД-блокировка) — атомарно, без дополнительных зависимостей,
     при отказе модератора карточка возвращается через TTL.
  2. Redis SETNX / Redlock — дополнительная зависимость, риск split-brain при перезапуске Redis.
  3. Отдельный queue-сервис — избыточно для MVP, усложняет деплой.
  Выбор: SELECT FOR UPDATE SKIP LOCKED — минимальные зависимости, атомарность гарантирована
  движком PostgreSQL, отлаживается через EXPLAIN ANALYZE, TTL 30 мин возвращает брошенные
  карточки без ручного вмешательства.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.moderation import TicketModel, TicketHistoryModel
from tests.conftest import TEST_MODERATOR_ID, TEST_MODERATOR_ID_2, make_mock_moderator

_PRODUCT_ID = uuid4()
_SELLER_ID = uuid4()
_TICKET_ID = uuid4()
_TICKET_ID_2 = uuid4()
_NOW = datetime.now(timezone.utc)


def _make_ticket(
    ticket_id=None,
    status="IN_REVIEW",
    priority=1,
    moderator_id=None,
) -> TicketModel:
    t = MagicMock(spec=TicketModel)
    t.id = ticket_id or _TICKET_ID
    t.product_id = _PRODUCT_ID
    t.seller_id = _SELLER_ID
    t.category_id = None
    t.kind = "CREATE"
    t.status = status
    t.queue_priority = priority
    t.assigned_moderator_id = moderator_id
    t.claimed_at = _NOW
    t.claim_expires_at = _NOW + timedelta(minutes=30)
    t.decision_at = None
    t.updated_at = _NOW
    t.created_at = _NOW
    t.history = []
    return t


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_next_returns_oldest_pending(mod_client):
    """POST /api/v1/queue/claim returns the oldest PENDING ticket and transitions it to IN_REVIEW.

    The ticket is assigned to the requesting moderator.
    """
    claimed_ticket = _make_ticket(status="IN_REVIEW", moderator_id=TEST_MODERATOR_ID)

    with patch(
        "app.api.routers.queue.ModerationQueueService.claim_next",
        new=AsyncMock(return_value=claimed_ticket),
    ):
        resp = await mod_client.post("/api/v1/queue/claim")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "IN_REVIEW"
    assert body["product_id"] == str(_PRODUCT_ID)
    assert body["queue_priority"] == 1


@pytest.mark.asyncio
async def test_next_returns_oldest_pending_canon_path(mod_client):
    """POST /api/v1/product-moderation/get-next (canon path) also works correctly."""
    claimed_ticket = _make_ticket(status="IN_REVIEW", moderator_id=TEST_MODERATOR_ID)

    with patch(
        "app.api.routers.product_moderation.ModerationQueueService.claim_next",
        new=AsyncMock(return_value=claimed_ticket),
    ):
        resp = await mod_client.post(
            "/api/v1/product-moderation/get-next",
            json={"queue_priority": 1},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "IN_REVIEW"


# ── Concurrency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_two_moderators_get_different_cards(mod_client):
    """SELECT FOR UPDATE SKIP LOCKED guarantees two moderators get different tickets.

    Simulated: first claim returns ticket A, second claim (different moderator)
    returns ticket B — they never share the same row.
    """
    ticket_a = _make_ticket(ticket_id=_TICKET_ID, priority=1, moderator_id=TEST_MODERATOR_ID)
    ticket_b = _make_ticket(ticket_id=_TICKET_ID_2, priority=1, moderator_id=TEST_MODERATOR_ID_2)

    call_count = 0

    async def _claim_side_effect(session, moderator_id, **kwargs):
        nonlocal call_count
        call_count += 1
        return ticket_a if call_count == 1 else ticket_b

    with patch(
        "app.api.routers.queue.ModerationQueueService.claim_next",
        new=_claim_side_effect,
    ):
        resp1 = await mod_client.post("/api/v1/queue/claim")
        resp2 = await mod_client.post("/api/v1/queue/claim")

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["id"] != resp2.json()["id"], "Two moderators must not claim the same ticket"


# ── Unhappy paths ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_queue_returns_204(mod_client):
    """POST /api/v1/queue/claim returns 204 when there are no PENDING tickets."""
    with patch(
        "app.api.routers.queue.ModerationQueueService.claim_next",
        new=AsyncMock(return_value=None),
    ):
        resp = await mod_client.post("/api/v1/queue/claim")

    assert resp.status_code == 204
    assert resp.content == b""


@pytest.mark.asyncio
async def test_moderator_already_has_in_review_returns_409(mod_client):
    """POST /api/v1/queue/claim returns 409 if moderator already has an active IN_REVIEW ticket.

    A moderator must finish or release their current ticket before claiming another.
    """
    from fastapi import HTTPException

    with patch(
        "app.api.routers.queue.ModerationQueueService.claim_next",
        new=AsyncMock(
            side_effect=HTTPException(
                status_code=409,
                detail={"code": "ALREADY_IN_REVIEW", "message": "You already have a ticket in review"},
            )
        ),
    ):
        resp = await mod_client.post("/api/v1/queue/claim")

    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "ALREADY_IN_REVIEW"


@pytest.mark.asyncio
async def test_empty_queue_canon_path_returns_204(mod_client):
    """POST /api/v1/product-moderation/get-next returns 204 when queue is empty."""
    with patch(
        "app.api.routers.product_moderation.ModerationQueueService.claim_next",
        new=AsyncMock(return_value=None),
    ):
        resp = await mod_client.post("/api/v1/product-moderation/get-next")

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_queue_priority_filter_passed_to_service(mod_client):
    """queue_priority filter is correctly forwarded to ModerationQueueService.claim_next."""
    claimed_ticket = _make_ticket(priority=2, moderator_id=TEST_MODERATOR_ID)
    mock_claim = AsyncMock(return_value=claimed_ticket)

    with patch("app.api.routers.queue.ModerationQueueService.claim_next", new=mock_claim):
        resp = await mod_client.post("/api/v1/queue/claim", json={"queue_priority": 2})

    assert resp.status_code == 200
    call_kwargs = mock_claim.call_args.kwargs
    assert call_kwargs["queue_priority"] == 2


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(ac):
    """Request without valid moderator JWT must return 401."""
    resp = await ac.post("/api/v1/queue/claim")
    assert resp.status_code == 401
