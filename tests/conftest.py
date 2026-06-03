"""Test fixtures for Moderation service tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.deps import get_session


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
