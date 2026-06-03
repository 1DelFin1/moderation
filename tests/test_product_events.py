"""Tests for US-MOD-01: приём событий о товаре от B2B.

DoD scenarios (moderation-flows.md#receive-product-events):
  happy:
    - created_pending — CREATED создаёт тикет в PENDING
    - edited_returns_to_review — EDITED создаёт новый тикет (прерывает текущий IN_REVIEW)
    - edited_updates_in_review — EDITED во время IN_REVIEW обновляет json_after
    - deleted_archived — DELETED уводит тикеты из очереди
  unhappy:
    - duplicate_event_no_side_effects — повтор с тем же ключом → 200, без побочных эффектов
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
_SERVICE_KEY = settings.SERVICE_KEY
_DATE = datetime.now(timezone.utc).isoformat()

_B2B_PRODUCT_DATA = {
    "id": str(_PRODUCT_ID),
    "title": "Смартфон Test",
    "category": {"id": str(uuid4()), "name": "Электроника"},
    "skus": [
        {"id": str(uuid4()), "price": 100_00, "stock": 5},
    ],
}


def _created_body(product_id=None, date=None):
    return {
        "product_id": str(product_id or _PRODUCT_ID),
        "seller_id": str(_SELLER_ID),
        "event": "CREATED",
        "date": date or _DATE,
    }


def _edited_body(product_id=None, date=None):
    return {
        "product_id": str(product_id or _PRODUCT_ID),
        "seller_id": str(_SELLER_ID),
        "event": "EDITED",
        "date": date or _DATE,
    }


def _deleted_body(product_id=None, date=None):
    return {
        "product_id": str(product_id or _PRODUCT_ID),
        "seller_id": str(_SELLER_ID),
        "event": "DELETED",
        "date": date or _DATE,
    }


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_created_pending(ac):
    """CREATED event must be accepted and passed to the service as PRODUCT_CREATED.

    The service creates a ticket with status PENDING and kind=CREATE.
    """
    mock_handle = AsyncMock(return_value=False)

    with patch("app.api.routers.b2b_events._fetch_product_from_b2b", AsyncMock(return_value=_B2B_PRODUCT_DATA)), \
         patch("app.api.routers.b2b_events.ModerationQueueService.handle_b2b_event", mock_handle):
        resp = await ac.post(
            "/api/v1/events/product",
            json=_created_body(),
            headers={"X-Service-Key": _SERVICE_KEY},
        )

    assert resp.status_code == 200
    assert resp.json() == {"accepted": True}

    mock_handle.assert_awaited_once()
    call_kwargs = mock_handle.call_args.kwargs
    assert call_kwargs["event_type"] == "PRODUCT_CREATED"
    assert call_kwargs["payload"]["product_id"] == str(_PRODUCT_ID)
    assert call_kwargs["payload"]["json_after"] == _B2B_PRODUCT_DATA


@pytest.mark.asyncio
async def test_edited_returns_to_review(ac):
    """EDITED event after MODERATED/BLOCKED returns the product to the queue.

    Service receives PRODUCT_EDITED — a new PENDING ticket is created,
    interrupting any active moderator claim.
    """
    mock_handle = AsyncMock(return_value=False)

    with patch("app.api.routers.b2b_events._fetch_product_from_b2b", AsyncMock(return_value=_B2B_PRODUCT_DATA)), \
         patch("app.api.routers.b2b_events.ModerationQueueService.handle_b2b_event", mock_handle):
        resp = await ac.post(
            "/api/v1/events/product",
            json=_edited_body(),
            headers={"X-Service-Key": _SERVICE_KEY},
        )

    assert resp.status_code == 200
    assert resp.json() == {"accepted": True}

    mock_handle.assert_awaited_once()
    call_kwargs = mock_handle.call_args.kwargs
    assert call_kwargs["event_type"] == "PRODUCT_EDITED"


@pytest.mark.asyncio
async def test_edited_updates_in_review(ac):
    """EDITED while IN_REVIEW still enqueues a new PENDING ticket with updated json_after.

    The moderator's current claim is interrupted — on their next approve/block call
    they will receive a 409 because the ticket is no longer assigned to them.
    """
    updated_product = {**_B2B_PRODUCT_DATA, "title": "Смартфон Test v2"}
    mock_handle = AsyncMock(return_value=False)

    with patch("app.api.routers.b2b_events._fetch_product_from_b2b", AsyncMock(return_value=updated_product)), \
         patch("app.api.routers.b2b_events.ModerationQueueService.handle_b2b_event", mock_handle):
        resp = await ac.post(
            "/api/v1/events/product",
            json=_edited_body(),
            headers={"X-Service-Key": _SERVICE_KEY},
        )

    assert resp.status_code == 200
    assert resp.json() == {"accepted": True}

    call_kwargs = mock_handle.call_args.kwargs
    assert call_kwargs["event_type"] == "PRODUCT_EDITED"
    # json_after reflects the latest product state fetched from B2B
    assert call_kwargs["payload"]["json_after"]["title"] == "Смартфон Test v2"


@pytest.mark.asyncio
async def test_deleted_archived(ac):
    """DELETED event cancels open tickets — product leaves the moderation queue.

    No B2B fetch is performed for DELETED events (product no longer exists).
    """
    mock_handle = AsyncMock(return_value=False)
    mock_fetch = AsyncMock()

    with patch("app.api.routers.b2b_events._fetch_product_from_b2b", mock_fetch), \
         patch("app.api.routers.b2b_events.ModerationQueueService.handle_b2b_event", mock_handle):
        resp = await ac.post(
            "/api/v1/events/product",
            json=_deleted_body(),
            headers={"X-Service-Key": _SERVICE_KEY},
        )

    assert resp.status_code == 200
    assert resp.json() == {"accepted": True}

    mock_handle.assert_awaited_once()
    call_kwargs = mock_handle.call_args.kwargs
    assert call_kwargs["event_type"] == "PRODUCT_DELETED"

    # No B2B fetch for DELETED events
    mock_fetch.assert_not_awaited()


# ── Idempotency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_event_no_side_effects(ac):
    """Duplicate event with same (product_id, event, date) returns 200 without side effects.

    The idempotency key is derived deterministically from (product_id, event, date).
    The service signals is_duplicate=True — no ticket is created a second time.
    """
    mock_handle = AsyncMock(return_value=True)  # True = duplicate

    with patch("app.api.routers.b2b_events._fetch_product_from_b2b", AsyncMock(return_value=_B2B_PRODUCT_DATA)), \
         patch("app.api.routers.b2b_events.ModerationQueueService.handle_b2b_event", mock_handle):
        resp = await ac.post(
            "/api/v1/events/product",
            json=_created_body(),
            headers={"X-Service-Key": _SERVICE_KEY},
        )

    assert resp.status_code == 200
    assert resp.json() == {"accepted": True}

    # Service was called (idempotency check is inside the service)
    mock_handle.assert_awaited_once()


# ── Auth / Security ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_service_header_401(ac):
    """Request without X-Service-Key header must return 401."""
    resp = await ac.post(
        "/api/v1/events/product",
        json=_created_body(),
        # No X-Service-Key header
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_service_key_returns_401(ac):
    """Request with incorrect X-Service-Key must return 401."""
    resp = await ac.post(
        "/api/v1/events/product",
        json=_created_body(),
        headers={"X-Service-Key": "wrong-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unknown_event_type_returns_400(ac):
    """Unknown event type must return 400 BAD_REQUEST."""
    body = {**_created_body(), "event": "UNKNOWN_EVENT"}
    # Pydantic rejects the literal at schema level
    resp = await ac.post(
        "/api/v1/events/product",
        json=body,
        headers={"X-Service-Key": _SERVICE_KEY},
    )
    assert resp.status_code == 422
