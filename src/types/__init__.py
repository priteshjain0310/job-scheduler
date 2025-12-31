"""
Type definitions for the job scheduler.
Contains input/output type definitions for all functions, grouped by module.
"""

from src.types.api import (
    AuthRequest,
    CreateJobRequest,
    CreateJobResponse,
    ErrorResponse,
    HealthResponse,
    JobListResponse,
    JobResponse,
    PaginationParams,
    RetryJobRequest,
    RetryJobResponse,
    TokenResponse,
)
from src.types.events import (
    JobEvent,
    WebSocketMessage,
)
from src.types.job import (
    JobContext,
    JobMetrics,
    JobPayload,
    JobResult,
    LeaseInfo,
)

__all__ = [
    # API types
    "CreateJobRequest",
    "CreateJobResponse",
    "JobResponse",
    "JobListResponse",
    "RetryJobRequest",
    "RetryJobResponse",
    "TokenResponse",
    "AuthRequest",
    "HealthResponse",
    "ErrorResponse",
    "PaginationParams",
    # Job types
    "JobPayload",
    "JobResult",
    "JobContext",
    "LeaseInfo",
    "JobMetrics",
    # Event types
    "JobEvent",
    "WebSocketMessage",
]
