"""
Application constants.
Centralized location for all constant values used across the application.
"""

from enum import StrEnum


class JobStatus(StrEnum):
    """
    Job lifecycle states.

    State transitions:
    - QUEUED -> LEASED (lease acquired)
    - LEASED -> RUNNING (execution started)
    - RUNNING -> SUCCEEDED (success)
    - RUNNING -> QUEUED (retry)
    - RUNNING -> DLQ (max attempts exceeded)
    - LEASED -> QUEUED (lease expired - crash recovery)
    """

    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DLQ = "dlq"


class JobPriority(StrEnum):
    """Job priority levels for queue ordering."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# Priority weights for ordering (higher = processed first)
PRIORITY_WEIGHTS: dict[JobPriority, int] = {
    JobPriority.LOW: 1,
    JobPriority.NORMAL: 5,
    JobPriority.HIGH: 10,
    JobPriority.CRITICAL: 100,
}

# Default values
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEASE_DURATION_SECONDS = 30
DEFAULT_PRIORITY = JobPriority.NORMAL

# API constants
API_V1_PREFIX = "/v1"
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
TENANT_ID_HEADER = "X-Tenant-ID"

# Metrics names
METRIC_QUEUE_DEPTH = "job_queue_depth"
METRIC_JOBS_SUBMITTED = "jobs_submitted_total"
METRIC_JOBS_COMPLETED = "jobs_completed_total"
METRIC_JOB_DURATION = "job_duration_seconds"
METRIC_LEASE_EXPIRED = "lease_expired_total"
METRIC_LEASE_ACQUIRED = "lease_acquired_total"
METRIC_API_REQUESTS = "api_requests_total"
METRIC_API_LATENCY = "api_request_latency_seconds"

# Trace span names
SPAN_SUBMIT_JOB = "submit_job"
SPAN_ACQUIRE_LEASE = "acquire_lease"
SPAN_EXECUTE_JOB = "execute_job"
SPAN_ACK_JOB = "ack_job"
SPAN_RELEASE_LEASE = "release_lease"

# WebSocket event types
WS_EVENT_JOB_CREATED = "job.created"
WS_EVENT_JOB_STARTED = "job.started"
WS_EVENT_JOB_COMPLETED = "job.completed"
WS_EVENT_JOB_FAILED = "job.failed"
WS_EVENT_JOB_DLQ = "job.dlq"
WS_EVENT_JOB_RETRIED = "job.retried"
