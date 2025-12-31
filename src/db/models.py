"""
SQLAlchemy database models.
Defines the Job table and other database entities.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.constants import JobPriority, JobStatus, PRIORITY_WEIGHTS


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


class Job(Base):
    """
    Job model representing a unit of work in the queue.
    
    This is the authoritative source of truth for job state.
    All job lifecycle transitions are managed through this table.
    
    Key constraints:
    - (tenant_id, idempotency_key) is unique for submission idempotency
    - status transitions follow the defined state machine
    - lease_owner and lease_expires_at track job leasing for at-least-once delivery
    """

    __tablename__ = "jobs"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        nullable=False,
    )

    # Tenant and idempotency
    tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Job payload
    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    # Status and priority
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", create_constraint=True, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=JobStatus.QUEUED,
        index=True,
    )
    priority: Mapped[JobPriority] = mapped_column(
        Enum(JobPriority, name="job_priority", create_constraint=True, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=JobPriority.NORMAL,
    )

    # Retry tracking
    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )

    # Lease management
    lease_owner: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    # Scheduling
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Error tracking
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Result storage (optional)
    result: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Table constraints and indexes
    __table_args__ = (
        # Idempotency constraint: unique per tenant
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_tenant_idempotency"),
        # Index for efficient queue polling
        Index(
            "ix_jobs_queue_poll",
            "status",
            "scheduled_at",
            "priority",
            postgresql_where=(Column("status") == JobStatus.QUEUED),
        ),
        # Index for tenant concurrency checks
        Index(
            "ix_jobs_tenant_active",
            "tenant_id",
            "status",
            postgresql_where=(
                Column("status").in_([JobStatus.LEASED, JobStatus.RUNNING])
            ),
        ),
        # Index for lease expiry checks
        Index(
            "ix_jobs_lease_expiry",
            "lease_expires_at",
            postgresql_where=(Column("status") == JobStatus.LEASED),
        ),
    )

    @property
    def priority_weight(self) -> int:
        """Get the numeric weight for this job's priority."""
        return PRIORITY_WEIGHTS.get(self.priority, 5)

    @property
    def is_retryable(self) -> bool:
        """Check if the job can be retried."""
        return self.attempt < self.max_attempts

    @property
    def is_lease_expired(self) -> bool:
        """Check if the job's lease has expired."""
        if self.lease_expires_at is None:
            return True
        return datetime.utcnow() > self.lease_expires_at.replace(tzinfo=None)

    def __repr__(self) -> str:
        return (
            f"Job(id={self.id}, tenant={self.tenant_id}, "
            f"status={self.status}, attempt={self.attempt}/{self.max_attempts})"
        )
