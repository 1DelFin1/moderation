"""Test fixtures for Moderation service tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.deps import get_session, get_current_active_auth_moderator
from app.models.moderators import ModeratorModel


TEST_MODERATOR_ID = uuid4()
TEST_MODERATOR_ID_2 = uuid4()


def make_mock_session():
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.scalars = AsyncMock()
    session.execute = AsyncMock()
    return session


def make_mock_moderator(moderator_id=None, role="MODERATOR") -> ModeratorModel:
    m = MagicMock(spec=ModeratorModel)
    m.id = moderator_id or TEST_MODERATOR_ID
    m.role = role
    m.is_active = True
    m.email = "mod@test.com"
    return m


@pytest_asyncio.fixture
async def ac():
    """Async test client with scheduler and DB mocked out."""
    mock_scheduler = MagicMock()
    mock_scheduler.start = MagicMock()
    mock_scheduler.shutdown = MagicMock()

    mock_session = make_mock_session()
    app.dependency_overrides[get_session] = lambda: mock_session

    with patch("app.scheduler.jobs.configure_scheduler", return_value=mock_scheduler):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def mod_client():
    """Async test client with moderator auth and scheduler mocked out."""
    mock_scheduler = MagicMock()
    mock_scheduler.start = MagicMock()
    mock_scheduler.shutdown = MagicMock()

    mock_session = make_mock_session()
    moderator = make_mock_moderator()

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_current_active_auth_moderator] = lambda: moderator

    with patch("app.scheduler.jobs.configure_scheduler", return_value=mock_scheduler):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client

    app.dependency_overrides.clear()
