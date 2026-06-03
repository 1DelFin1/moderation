"""Tests for US-MOD-06: справочник причин блокировки.

DoD scenarios (moderation-flows.md#blocking-reasons):
  happy:
    - list_returns_active_reasons — активные причины с полями id, title, hard_block
  unhappy:
    - inactive_reasons_not_visible — деактивированные скрыты по умолчанию
    - referenced_reason_cannot_be_deleted — удаление = мягкая деактивация (is_active=False)

ADR (хранение справочника):
  Рассмотрено три подхода:
  1. Enum в коде — просто, но требует миграции и деплоя при каждом изменении.
     Исторические ссылки не сломаются, но нет i18n.
  2. Таблица в БД с CRUD-админкой — новые причины добавляются без деплоя,
     FK на тикеты не ломают историю при soft-delete (is_active=False).
     Поддержка i18n через отдельную таблицу переводов.
  3. i18n-каталог — избыточно для MVP.
  Выбор: таблица в БД + soft-delete — новые причины без деплоя, исторические
  BLOCKED-карточки сохраняют ссылку даже после деактивации причины,
  аналитика работает по id.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.moderation import ProductBlockingReasonModel

_REASON_ID_1 = uuid4()
_REASON_ID_2 = uuid4()
_REASON_ID_3 = uuid4()


def _make_reason(reason_id=None, hard_block=False, is_active=True, title="Тест") -> ProductBlockingReasonModel:
    r = MagicMock(spec=ProductBlockingReasonModel)
    r.id = reason_id or uuid4()
    r.code = "TEST_REASON"
    r.title = title
    r.description = None
    r.hard_block = hard_block
    r.is_active = is_active
    return r


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_active_reasons(mod_client):
    """GET /api/v1/product-blocking-reasons returns active blocking reasons with id, title, hard_block."""
    active_reasons = [
        _make_reason(_REASON_ID_1, hard_block=False, title="Фото не соответствует"),
        _make_reason(_REASON_ID_2, hard_block=True, title="Контрафактный товар"),
    ]

    with patch(
        "app.api.routers.blocking_reasons.ModerationQueueService.get_blocking_reasons",
        AsyncMock(return_value=active_reasons),
    ):
        resp = await mod_client.get("/api/v1/product-blocking-reasons")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["id"] == str(_REASON_ID_1)
    assert body[0]["title"] == "Фото не соответствует"
    assert body[0]["hard_block"] is False
    assert body[1]["hard_block"] is True


@pytest.mark.asyncio
async def test_list_returns_active_reasons_openapi_path(mod_client):
    """GET /api/v1/blocking-reasons (OpenAPI path) also returns active reasons."""
    active_reasons = [_make_reason(_REASON_ID_1, title="Причина")]

    with patch(
        "app.api.routers.blocking_reasons.ModerationQueueService.get_blocking_reasons",
        AsyncMock(return_value=active_reasons),
    ):
        resp = await mod_client.get("/api/v1/blocking-reasons")

    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_list_filter_hard_block_true(mod_client):
    """GET ?hard_block=true returns only hard-block reasons."""
    hard_reasons = [_make_reason(_REASON_ID_2, hard_block=True)]
    mock_get = AsyncMock(return_value=hard_reasons)

    with patch("app.api.routers.blocking_reasons.ModerationQueueService.get_blocking_reasons", mock_get):
        resp = await mod_client.get("/api/v1/product-blocking-reasons?hard_block=true")

    assert resp.status_code == 200
    assert resp.json()[0]["hard_block"] is True
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["hard_block"] is True


@pytest.mark.asyncio
async def test_list_filter_hard_block_false(mod_client):
    """GET ?hard_block=false returns only soft-block reasons."""
    soft_reasons = [_make_reason(_REASON_ID_1, hard_block=False)]
    mock_get = AsyncMock(return_value=soft_reasons)

    with patch("app.api.routers.blocking_reasons.ModerationQueueService.get_blocking_reasons", mock_get):
        resp = await mod_client.get("/api/v1/product-blocking-reasons?hard_block=false")

    assert resp.status_code == 200
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["hard_block"] is False


# ── Unhappy paths ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inactive_reasons_not_visible(mod_client):
    """Deactivated reasons are hidden by default (is_active=True filter).

    By default, GET returns only is_active=True reasons.
    Deactivated reasons require explicit ?is_active=false to appear.
    """
    # Default request (is_active=True) returns only active
    active_only = [_make_reason(_REASON_ID_1, is_active=True)]
    mock_get = AsyncMock(return_value=active_only)

    with patch("app.api.routers.blocking_reasons.ModerationQueueService.get_blocking_reasons", mock_get):
        resp = await mod_client.get("/api/v1/product-blocking-reasons")

    assert resp.status_code == 200
    body = resp.json()
    assert all(r["is_active"] for r in body)

    # Service was called with is_active=True
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["is_active"] is True


@pytest.mark.asyncio
async def test_inactive_reasons_visible_with_filter(mod_client):
    """Deactivated reasons appear when ?is_active=false is explicitly passed."""
    inactive_reasons = [_make_reason(_REASON_ID_3, is_active=False, title="Удалённая причина")]
    mock_get = AsyncMock(return_value=inactive_reasons)

    with patch("app.api.routers.blocking_reasons.ModerationQueueService.get_blocking_reasons", mock_get):
        resp = await mod_client.get("/api/v1/product-blocking-reasons?is_active=false")

    assert resp.status_code == 200
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["is_active"] is False


@pytest.mark.asyncio
async def test_referenced_reason_cannot_be_deleted():
    """DELETE sets is_active=False (soft delete) instead of physically removing.

    Preserves historical references from BLOCKED tickets to this reason.
    Admin endpoint — requires ADMIN role.
    """
    from unittest.mock import MagicMock, patch
    from app.api.deps import get_current_active_auth_admin_moderator, get_session
    from tests.conftest import make_mock_moderator, make_mock_session
    from app.main import app
    from app.scheduler.jobs import configure_scheduler
    from httpx import AsyncClient, ASGITransport

    admin_moderator = make_mock_moderator(role="ADMIN")
    mock_session = make_mock_session()
    mock_scheduler = MagicMock()
    mock_scheduler.start = MagicMock()
    mock_scheduler.shutdown = MagicMock()

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_current_active_auth_admin_moderator] = lambda: admin_moderator

    mock_delete = AsyncMock(return_value=None)

    with patch("app.scheduler.jobs.configure_scheduler", return_value=mock_scheduler), \
         patch("app.api.routers.blocking_reasons.ModerationQueueService.delete_blocking_reason", mock_delete):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(f"/api/v1/product-blocking-reasons/{_REASON_ID_1}")

    app.dependency_overrides.clear()

    assert resp.status_code == 204
    # delete_blocking_reason sets is_active=False (not physical DELETE)
    mock_delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(ac):
    """GET without auth returns 401."""
    resp = await ac.get("/api/v1/product-blocking-reasons")
    assert resp.status_code == 401
