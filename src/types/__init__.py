"""
Type definitions for the job scheduler.
Contains input/output type definitions for all functions, grouped by module.
"""

from src.types.api import (
    CreateJobRequest,
    CreateJobResponse,
    JobResponse,
    JobListResponse,
    RetryJobRequest,
    RetryJobResponse,
    TokenResponse,
    AuthRequest,
    HealthResponse,
    ErrorResponse,
    PaginationParams,
)
from src.types.job import (
    JobPayload,
    JobResult,
    JobContext,
    LeaseInfo,
    JobMetrics,
)
from src.types.events import (
    JobEvent,
    WebSocketMessage,
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
