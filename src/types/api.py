"""
API request and response type definitions.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from src.constants import JobPriority, JobStatus


class CreateJobRequest(BaseModel):
    """Request body for creating a new job."""

    payload: dict[str, Any] = Field(..., description="Job payload data")
    max_attempts: int = Field(default=3, ge=1, le=10, description="Maximum retry attempts")
    priority: JobPriority = Field(default=JobPriority.NORMAL, description="Job priority")
    scheduled_at: datetime | None = Field(
        default=None, description="Schedule job for future execution"
    )


class CreateJobResponse(BaseModel):
    """Response body after creating a job."""

    id: UUID
    tenant_id: str
    idempotency_key: str
    status: JobStatus
    created_at: datetime
    message: str = "Job created successfully"


class JobResponse(BaseModel):
    """Full job details response."""

    id: UUID
    tenant_id: str
    idempotency_key: str
    payload: dict[str, Any]
    status: JobStatus
    priority: JobPriority
    attempt: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    scheduled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    last_error: str | None


class JobListResponse(BaseModel):
    """Paginated list of jobs."""

    jobs: list[JobResponse]
    total: int
    page: int
    page_size: int
    has_next: bool


class RetryJobRequest(BaseModel):
    """Request body for retrying a job from DLQ."""

    reset_attempts: bool = Field(
        default=True, description="Reset attempt counter to 0"
    )


class RetryJobResponse(BaseModel):
    """Response body after retrying a job."""

    id: UUID
    status: JobStatus
    attempt: int
    message: str = "Job queued for retry"


class AuthRequest(BaseModel):
    """Authentication request."""

    api_key: str = Field(..., description="API key for authentication")
    tenant_id: str = Field(..., description="Tenant identifier")


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    database: str
    timestamp: datetime


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: str | None = None
    request_id: str | None = None


class PaginationParams(BaseModel):
    """Pagination parameters."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    status: JobStatus | None = None
