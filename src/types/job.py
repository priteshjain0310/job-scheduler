"""
Job-related type definitions for internal use.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from src.constants import JobPriority, JobStatus


class JobPayload(BaseModel):
    """
    Job payload structure.
    Contains the actual work to be executed by workers.
    """

    job_type: str
    data: dict[str, Any]
    metadata: dict[str, Any] | None = None


class JobResult(BaseModel):
    """
    Result of job execution.
    Returned by job handlers after processing.
    """

    success: bool
    output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: float | None = None


@dataclass
class JobContext:
    """
    Context passed to job handlers during execution.
    Contains job metadata and utilities for the handler.
    """

    job_id: UUID
    tenant_id: str
    attempt: int
    max_attempts: int
    payload: dict[str, Any]
    lease_owner: str
    lease_expires_at: datetime

    @property
    def is_last_attempt(self) -> bool:
        """Check if this is the last retry attempt."""
        return self.attempt >= self.max_attempts

    @property
    def remaining_attempts(self) -> int:
        """Get remaining retry attempts."""
        return max(0, self.max_attempts - self.attempt)


@dataclass
class LeaseInfo:
    """
    Information about a job lease.
    Used by workers to track their leased jobs.
    """

    job_id: UUID
    tenant_id: str
    lease_owner: str
    lease_expires_at: datetime
    acquired_at: datetime

    @property
    def is_expired(self) -> bool:
        """Check if the lease has expired."""
        return datetime.utcnow() > self.lease_expires_at

    @property
    def time_remaining_seconds(self) -> float:
        """Get remaining time on the lease in seconds."""
        remaining = (self.lease_expires_at - datetime.utcnow()).total_seconds()
        return max(0.0, remaining)


class JobMetrics(BaseModel):
    """
    Metrics for a completed job.
    Used for observability and reporting.
    """

    job_id: UUID
    tenant_id: str
    status: JobStatus
    priority: JobPriority
    attempts: int
    total_duration_ms: float
    execution_duration_ms: float
    queue_wait_time_ms: float
    created_at: datetime
    completed_at: datetime
