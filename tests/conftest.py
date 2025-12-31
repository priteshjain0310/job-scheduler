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
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from src.api.auth import create_access_token
from src.api.main import create_app
from src.config import Settings, get_settings
from src.db import init_db, close_db
from src.db.models import Base


# Test database URL (can use testcontainers or local postgres)
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/jobqueue_test"
)


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    """
    Start a PostgreSQL container for integration tests.
    
    Only used when running integration tests that need a real database.
    """
    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    """Get the database URL from the container."""
    # Convert to async URL
    sync_url = postgres_container.get_connection_url()
    return sync_url.replace("postgresql://", "postgresql+asyncpg://")


@pytest_asyncio.fixture
async def async_engine(database_url: str):
    """Create an async database engine for tests."""
    engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
    )
    
    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    # Drop all tables after tests
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a database session for tests."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    
    async with session_factory() as session:
        yield session
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


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app for testing."""
    return create_app()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
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
