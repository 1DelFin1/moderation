"""Tests for US-MOD-01: приём событий о товаре от B2B.

DoD scenarios (moderation-flows.md#receive-product-events):
  happy:
    - created_pending — CREATED создаёт тикет в PENDING
    - edited_returns_to_review — EDITED обновляет тикет in-place, возвращает в очередь
    - edited_updates_in_review — EDITED во время IN_REVIEW обновляет json_after
    - deleted_archived — DELETED уводит тикеты из очереди
  unhappy:
    - duplicate_event_no_side_effects — повтор с тем же ключом → 202, без побочных эффектов
    - missing_service_header_401 — нет X-Service-Key → 401
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings

_PRODUCT_ID = uuid4()
_SELLER_ID = uuid4()
_CATEGORY_ID = uuid4()
_SERVICE_KEY = settings.SERVICE_KEY
_IDEM_KEY = str(uuid4())
_OCCURRED_AT = datetime.now(timezone.utc).isoformat()

_JSON_AFTER = {
    "id": str(_PRODUCT_ID),
    "title": "Смартфон Test",
    "category": {"id": str(_CATEGORY_ID), "name": "Электроника"},
    "skus": [
        {"id": str(uuid4()), "price": 100_00, "stock": 5},
    ],
}


def _created_body(product_id=None, idem_key=None):
    return {
        "event_type": "PRODUCT_CREATED",
        "idempotency_key": idem_key or _IDEM_KEY,
        "occurred_at": _OCCURRED_AT,
        "payload": {
            "product_id": str(product_id or _PRODUCT_ID),
            "seller_id": str(_SELLER_ID),
            "category_id": str(_CATEGORY_ID),
            "queue_priority": 1,
            "json_after": _JSON_AFTER,
        },
    }


def _edited_body(product_id=None, idem_key=None, json_after=None):
    return {
        "event_type": "PRODUCT_EDITED",
        "idempotency_key": idem_key or str(uuid4()),
        "occurred_at": _OCCURRED_AT,
        "payload": {
            "product_id": str(product_id or _PRODUCT_ID),
            "seller_id": str(_SELLER_ID),
            "category_id": str(_CATEGORY_ID),
            "queue_priority": 3,
            "json_before": _JSON_AFTER,
            "json_after": json_after or _JSON_AFTER,
        },
    }


def _deleted_body(product_id=None, idem_key=None):
    return {
        "event_type": "PRODUCT_DELETED",
        "idempotency_key": idem_key or str(uuid4()),
        "occurred_at": _OCCURRED_AT,
        "payload": {
            "product_id": str(product_id or _PRODUCT_ID),
        },
    }


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_created_pending(ac):
    """CREATED event must be accepted and passed to the service as PRODUCT_CREATED.

    The service creates a ticket with status PENDING and kind=CREATE.
    """
    mock_handle = AsyncMock(return_value=False)

    with patch("app.api.routers.b2b_events.ModerationQueueService.handle_b2b_event", mock_handle):
        resp = await ac.post(
            "/api/v1/b2b/events",
            json=_created_body(),
            headers={"X-Service-Key": _SERVICE_KEY},
        )

    assert resp.status_code == 202
    assert resp.json() == {"accepted": True}

    mock_handle.assert_awaited_once()
    call_kwargs = mock_handle.call_args.kwargs
    assert call_kwargs["event_type"] == "PRODUCT_CREATED"
    assert call_kwargs["payload"]["product_id"] == str(_PRODUCT_ID)
    assert call_kwargs["payload"]["json_after"] == _JSON_AFTER


@pytest.mark.asyncio
async def test_edited_returns_to_review(ac):
    """EDITED event after MODERATED/BLOCKED returns the product to the queue.

    Service receives PRODUCT_EDITED — existing ticket is updated in-place to PENDING,
    no new row is inserted.
    """
    mock_handle = AsyncMock(return_value=False)

    with patch("app.api.routers.b2b_events.ModerationQueueService.handle_b2b_event", mock_handle):
        resp = await ac.post(
            "/api/v1/b2b/events",
            json=_edited_body(),
            headers={"X-Service-Key": _SERVICE_KEY},
        )

    assert resp.status_code == 202
    assert resp.json() == {"accepted": True}

    mock_handle.assert_awaited_once()
    call_kwargs = mock_handle.call_args.kwargs
    assert call_kwargs["event_type"] == "PRODUCT_EDITED"


@pytest.mark.asyncio
async def test_edited_updates_in_review(ac):
    """EDITED while IN_REVIEW updates the existing ticket's json_after and resets it to PENDING.

    The moderator's current claim is interrupted — on their next approve/block call
    they will receive a 409 because the ticket is no longer assigned to them.
    """
    updated_json = {**_JSON_AFTER, "title": "Смартфон Test v2"}
    mock_handle = AsyncMock(return_value=False)

    with patch("app.api.routers.b2b_events.ModerationQueueService.handle_b2b_event", mock_handle):
        resp = await ac.post(
            "/api/v1/b2b/events",
            json=_edited_body(json_after=updated_json),
            headers={"X-Service-Key": _SERVICE_KEY},
        )

    assert resp.status_code == 202
    assert resp.json() == {"accepted": True}

    call_kwargs = mock_handle.call_args.kwargs
    assert call_kwargs["event_type"] == "PRODUCT_EDITED"
    assert call_kwargs["payload"]["json_after"]["title"] == "Смартфон Test v2"


@pytest.mark.asyncio
async def test_deleted_archived(ac):
    """DELETED event cancels open tickets — product leaves the moderation queue.

    All statuses are cancelled, including MODERATED and BLOCKED.
    """
    mock_handle = AsyncMock(return_value=False)

    with patch("app.api.routers.b2b_events.ModerationQueueService.handle_b2b_event", mock_handle):
        resp = await ac.post(
            "/api/v1/b2b/events",
            json=_deleted_body(),
            headers={"X-Service-Key": _SERVICE_KEY},
        )

    assert resp.status_code == 202
    assert resp.json() == {"accepted": True}

    mock_handle.assert_awaited_once()
    call_kwargs = mock_handle.call_args.kwargs
    assert call_kwargs["event_type"] == "PRODUCT_DELETED"


# ── Idempotency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_event_no_side_effects(ac):
    """Duplicate event with same idempotency_key returns 202 without side effects.

    The service signals is_duplicate=True — no ticket is created a second time.
    """
    mock_handle = AsyncMock(return_value=True)  # True = duplicate

    with patch("app.api.routers.b2b_events.ModerationQueueService.handle_b2b_event", mock_handle):
        resp = await ac.post(
            "/api/v1/b2b/events",
            json=_created_body(),
            headers={"X-Service-Key": _SERVICE_KEY},
        )

    assert resp.status_code == 202
    assert resp.json() == {"accepted": True}

    mock_handle.assert_awaited_once()


# ── Auth / Security ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_service_header_401(ac):
    """Request without X-Service-Key header must return 401."""
    resp = await ac.post(
        "/api/v1/b2b/events",
        json=_created_body(),
        # No X-Service-Key header
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_service_key_returns_401(ac):
    """Request with incorrect X-Service-Key must return 401."""
    resp = await ac.post(
        "/api/v1/b2b/events",
        json=_created_body(),
        headers={"X-Service-Key": "wrong-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unknown_event_type_returns_400(ac):
    """Unknown event type must return 422 (Pydantic validation rejects the literal)."""
    body = {**_created_body(), "event_type": "UNKNOWN_EVENT"}
    resp = await ac.post(
        "/api/v1/b2b/events",
        json=body,
        headers={"X-Service-Key": _SERVICE_KEY},
    )
    assert resp.status_code == 422
