"""
Pytest configuration and shared fixtures.
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.api.auth import create_access_token
from src.api.main import create_app
from src.config import Settings
from src.db import close_db, init_db

# Test database URL - use separate test database
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/jobqueue_test"
)

# Set DATABASE_URL environment variable BEFORE any imports that might initialize the database
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop]:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    """Get the test database URL."""
    return TEST_DATABASE_URL


@pytest_asyncio.fixture
async def async_engine(database_url: str):
    """Create an async database engine for tests."""
    engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
    )

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession]:
    """Create a database session for tests."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        # Clean up test data before each test to ensure clean state
        await session.execute(sa.text("TRUNCATE TABLE jobs RESTART IDENTITY CASCADE"))
        await session.commit()

        yield session

        # Rollback any uncommitted changes
        await session.rollback()


@pytest.fixture
def test_settings() -> Settings:
    """Create test settings."""
    return Settings(
        database_url=TEST_DATABASE_URL,
        api_secret_key="test-secret-key",
        log_level="DEBUG",
        log_format="console",
        worker_lease_duration_seconds=5,
        worker_poll_interval_seconds=0.1,
        reaper_interval_seconds=1,
    )


@pytest_asyncio.fixture
async def app(database_url: str) -> AsyncGenerator[FastAPI]:
    """Create a FastAPI app for testing with initialized database."""
    # Save original DATABASE_URL
    original_db_url = os.environ.get("DATABASE_URL")

    # Override settings with test database URL
    os.environ["DATABASE_URL"] = database_url

    # Clear any cached engine/session
    from src.db.connection import _engine
    global_engine = _engine
    if global_engine:
        await global_engine.dispose()

    # Initialize database with test URL
    await init_db()

    app = create_app()
    yield app

    # Cleanup
    await close_db()

    # Restore original DATABASE_URL
    if original_db_url:
        os.environ["DATABASE_URL"] = original_db_url
    else:
        os.environ.pop("DATABASE_URL", None)


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient]:
    """Create an async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def test_tenant_id() -> str:
    """Generate a test tenant ID."""
    return f"test-tenant-{uuid4().hex[:8]}"


@pytest.fixture
def auth_headers(test_tenant_id: str) -> dict[str, str]:
    """Create authentication headers for testing."""
    token = create_access_token(tenant_id=test_tenant_id)
    return {
        "Authorization": f"Bearer {token}",
    }


@pytest.fixture
def idempotency_key() -> str:
    """Generate a unique idempotency key."""
    return f"test-{uuid4().hex}"


@pytest.fixture
def sample_job_payload() -> dict[str, Any]:
    """Create a sample job payload."""
    return {
        "job_type": "echo",
        "data": {"message": "Hello, World!"},
    }
