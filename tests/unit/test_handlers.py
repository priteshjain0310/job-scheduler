"""
Unit tests for job handlers.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from src.types.job import JobContext
from src.worker.handlers import (
    execute_job,
    get_handler,
    handle_echo,
    handle_failing_job,
    list_handlers,
)


class TestJobHandlers:
    """Tests for job handlers."""

    @pytest.fixture
    def job_context(self) -> JobContext:
        """Create a test job context."""
        return JobContext(
            job_id=uuid4(),
            tenant_id="test-tenant",
            attempt=1,
            max_attempts=3,
            payload={"job_type": "echo", "data": {"message": "test"}},
            lease_owner="test-worker",
            lease_expires_at=datetime.utcnow() + timedelta(seconds=30),
        )

    def test_list_handlers(self):
        """Test listing registered handlers."""
        handlers = list_handlers()

        assert "echo" in handlers
        assert "sleep" in handlers
        assert "failing_job" in handlers

    def test_get_handler_exists(self):
        """Test getting an existing handler."""
        handler = get_handler("echo")
        assert handler is not None
        assert handler == handle_echo

    def test_get_handler_not_exists(self):
        """Test getting a non-existent handler."""
        handler = get_handler("nonexistent")
        assert handler is None

    @pytest.mark.asyncio
    async def test_echo_handler(self, job_context: JobContext):
        """Test the echo handler."""
        result = await handle_echo(job_context)

        assert result.success is True
        assert result.output == {"echo": job_context.payload}

    @pytest.mark.asyncio
    async def test_failing_handler(self, job_context: JobContext):
        """Test the failing job handler."""
        result = await handle_failing_job(job_context)

        assert result.success is False
        assert "Intentional failure" in result.error

    @pytest.mark.asyncio
    async def test_execute_job_with_valid_type(self, job_context: JobContext):
        """Test execute_job with a valid job type."""
        job_context.payload["job_type"] = "echo"

        result = await execute_job(job_context)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_job_with_invalid_type(self, job_context: JobContext):
        """Test execute_job with an invalid job type."""
        job_context.payload["job_type"] = "nonexistent_handler"

        result = await execute_job(job_context)

        assert result.success is False
        assert "No handler registered" in result.error


class TestJobContext:
    """Tests for JobContext."""

    def test_is_last_attempt(self):
        """Test is_last_attempt property."""
        context = JobContext(
            job_id=uuid4(),
            tenant_id="test",
            attempt=3,
            max_attempts=3,
            payload={},
            lease_owner="worker",
            lease_expires_at=datetime.utcnow() + timedelta(seconds=30),
        )

        assert context.is_last_attempt is True

    def test_remaining_attempts(self):
        """Test remaining_attempts property."""
        context = JobContext(
            job_id=uuid4(),
            tenant_id="test",
            attempt=1,
            max_attempts=3,
            payload={},
            lease_owner="worker",
            lease_expires_at=datetime.utcnow() + timedelta(seconds=30),
        )

        assert context.remaining_attempts == 2
