"""
Event type definitions for WebSocket and internal messaging.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from src.constants import JobStatus


class JobEvent(BaseModel):
    """
    Event emitted when job state changes.
    Used for WebSocket notifications and internal event handling.
    """

    event_type: str
    job_id: UUID
    tenant_id: str
    status: JobStatus
    timestamp: datetime
    data: dict[str, Any] | None = None

    @classmethod
    def job_created(
        cls,
        job_id: UUID,
        tenant_id: str,
        payload: dict[str, Any],
    ) -> "JobEvent":
        """Create a job created event."""
        return cls(
            event_type="job.created",
            job_id=job_id,
            tenant_id=tenant_id,
            status=JobStatus.QUEUED,
            timestamp=datetime.utcnow(),
            data={"payload": payload},
        )

    @classmethod
    def job_started(
        cls,
        job_id: UUID,
        tenant_id: str,
        worker_id: str,
        attempt: int,
    ) -> "JobEvent":
        """Create a job started event."""
        return cls(
            event_type="job.started",
            job_id=job_id,
            tenant_id=tenant_id,
            status=JobStatus.RUNNING,
            timestamp=datetime.utcnow(),
            data={"worker_id": worker_id, "attempt": attempt},
        )

    @classmethod
    def job_completed(
        cls,
        job_id: UUID,
        tenant_id: str,
        result: dict[str, Any] | None = None,
    ) -> "JobEvent":
        """Create a job completed event."""
        return cls(
            event_type="job.completed",
            job_id=job_id,
            tenant_id=tenant_id,
            status=JobStatus.SUCCEEDED,
            timestamp=datetime.utcnow(),
            data={"result": result},
        )

    @classmethod
    def job_failed(
        cls,
        job_id: UUID,
        tenant_id: str,
        error: str,
        attempt: int,
        will_retry: bool,
    ) -> "JobEvent":
        """Create a job failed event."""
        return cls(
            event_type="job.failed",
            job_id=job_id,
            tenant_id=tenant_id,
            status=JobStatus.FAILED if not will_retry else JobStatus.QUEUED,
            timestamp=datetime.utcnow(),
            data={"error": error, "attempt": attempt, "will_retry": will_retry},
        )

    @classmethod
    def job_dlq(
        cls,
        job_id: UUID,
        tenant_id: str,
        error: str,
        attempts: int,
    ) -> "JobEvent":
        """Create a job moved to DLQ event."""
        return cls(
            event_type="job.dlq",
            job_id=job_id,
            tenant_id=tenant_id,
            status=JobStatus.DLQ,
            timestamp=datetime.utcnow(),
            data={"error": error, "total_attempts": attempts},
        )


class WebSocketMessage(BaseModel):
    """
    Message format for WebSocket communication.
    """

    type: str
    payload: dict[str, Any]
    timestamp: datetime

    @classmethod
    def from_event(cls, event: JobEvent) -> "WebSocketMessage":
        """Create a WebSocket message from a job event."""
        return cls(
            type=event.event_type,
            payload={
                "job_id": str(event.job_id),
                "tenant_id": event.tenant_id,
                "status": event.status,
                "data": event.data,
            },
            timestamp=event.timestamp,
        )
