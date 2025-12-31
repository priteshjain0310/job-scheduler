"""
Database connection management.
Handles async SQLAlchemy engine and session creation.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.config import get_settings

logger = logging.getLogger(__name__)

# Global engine instance
_engine: AsyncEngine | None = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """
    Get or create the async database engine.
    
    Returns:
        AsyncEngine: The SQLAlchemy async engine instance.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            echo=settings.log_level == "DEBUG",
            pool_pre_ping=True,
        )
    return _engine


def get_test_engine(database_url: str) -> AsyncEngine:
    """
    Create a test database engine with NullPool.
    
    Args:
        database_url: The database URL for testing.
        
    Returns:
        AsyncEngine: The test SQLAlchemy async engine instance.
    """
    return create_async_engine(
        database_url,
        poolclass=NullPool,
        echo=False,
    )


async def init_db() -> None:
    """
    Initialize the database connection and session factory.
    Should be called on application startup.
    """
    global AsyncSessionLocal
    engine = get_engine()
    AsyncSessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    logger.info("Database connection initialized")


async def close_db() -> None:
    """
    Close the database connection.
    Should be called on application shutdown.
    """
    global _engine, AsyncSessionLocal
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        AsyncSessionLocal = None
        logger.info("Database connection closed")


async def get_async_session() -> AsyncGenerator[AsyncSession]:
    """
    Dependency for getting async database sessions.
    
    Yields:
        AsyncSession: An async database session.
        
    Raises:
        RuntimeError: If the database is not initialized.
    """
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_session_context() -> AsyncGenerator[AsyncSession]:
    """
    Context manager for getting async database sessions.
    Useful for non-FastAPI contexts like workers.
    
    Yields:
        AsyncSession: An async database session.
    """
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
