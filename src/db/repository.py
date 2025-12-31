"""
Job repository for database operations.
Implements the core data access patterns for job management.
"""

import logging
from datetime import datetime, timedelta
from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select, update, and_, or_, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.constants import JobStatus, JobPriority, PRIORITY_WEIGHTS
from src.db.models import Job

logger = logging.getLogger(__name__)


class JobRepository:
    """
    Repository for job database operations.
    
    Implements atomic operations for:
    - Job submission with idempotency
    - Lease acquisition with FOR UPDATE SKIP LOCKED
    - Status transitions
    - Lease expiry handling
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize the repository with a database session.
        
        Args:
            session: The async database session.
        """
        self._session = session
        self._settings = get_settings()

    async def create_job(
        self,
        tenant_id: str,
        idempotency_key: str,
        payload: dict,
        max_attempts: int = 3,
        priority: JobPriority = JobPriority.NORMAL,
        scheduled_at: datetime | None = None,
    ) -> tuple[Job, bool]:
        """
        Create a new job with idempotency support.
        
        Uses INSERT ... ON CONFLICT DO NOTHING to ensure exactly-once submission.
        
        Args:
            tenant_id: The tenant identifier.
            idempotency_key: Unique key for idempotent submission.
            payload: The job payload.
            max_attempts: Maximum retry attempts.
            priority: Job priority level.
            scheduled_at: Optional scheduled execution time.
            
        Returns:
            Tuple of (Job, created) where created is True if new job was created.
        """
        # Try to insert, handling conflict on idempotency key
        stmt = insert(Job).values(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            payload=payload,
            max_attempts=max_attempts,
            priority=priority,
            scheduled_at=scheduled_at or datetime.utcnow(),
            status=JobStatus.QUEUED,
        ).on_conflict_do_nothing(
            constraint="uq_tenant_idempotency"
        ).returning(Job)
        
        result = await self._session.execute(stmt)
        job = result.scalar_one_or_none()
        
        if job is not None:
            logger.info(
                "Created new job",
                extra={"job_id": str(job.id), "tenant_id": tenant_id}
            )
            return job, True
        
        # Job already exists, fetch it
        existing = await self.get_job_by_idempotency_key(tenant_id, idempotency_key)
        if existing is None:
            raise RuntimeError("Job should exist after conflict")
        
        logger.info(
            "Returned existing job (idempotent)",
            extra={"job_id": str(existing.id), "tenant_id": tenant_id}
        )
        return existing, False

    async def get_job(self, job_id: UUID) -> Job | None:
        """
        Get a job by ID.
        
        Args:
            job_id: The job UUID.
            
        Returns:
            The Job or None if not found.
        """
        stmt = select(Job).where(Job.id == job_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_job_by_idempotency_key(
        self,
        tenant_id: str,
        idempotency_key: str,
    ) -> Job | None:
        """
        Get a job by tenant and idempotency key.
        
        Args:
            tenant_id: The tenant identifier.
            idempotency_key: The idempotency key.
            
        Returns:
            The Job or None if not found.
        """
        stmt = select(Job).where(
            and_(
                Job.tenant_id == tenant_id,
                Job.idempotency_key == idempotency_key,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_jobs(
        self,
        tenant_id: str,
        status: JobStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[Sequence[Job], int]:
        """
        List jobs for a tenant with optional filtering.
        
        Args:
            tenant_id: The tenant identifier.
            status: Optional status filter.
            limit: Maximum number of jobs to return.
            offset: Offset for pagination.
            
        Returns:
            Tuple of (jobs, total_count).
        """
        # Build base query
        base_filter = Job.tenant_id == tenant_id
        if status is not None:
            base_filter = and_(base_filter, Job.status == status)
        
        # Get total count
        count_stmt = select(func.count()).select_from(Job).where(base_filter)
        count_result = await self._session.execute(count_stmt)
        total = count_result.scalar() or 0
        
        # Get jobs
        stmt = (
            select(Job)
            .where(base_filter)
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        jobs = result.scalars().all()
        
        return jobs, total

    async def acquire_lease(
        self,
        worker_id: str,
        tenant_id: str | None = None,
        batch_size: int = 1,
    ) -> Sequence[Job]:
        """
        Acquire lease on available jobs using FOR UPDATE SKIP LOCKED.
        
        This is the critical path for job distribution. Uses atomic operations
        to prevent double-leasing.
        
        Args:
            worker_id: The worker identifier.
            tenant_id: Optional tenant filter.
            batch_size: Number of jobs to acquire.
            
        Returns:
            List of leased jobs.
        """
        lease_duration = timedelta(
            seconds=self._settings.worker_lease_duration_seconds
        )
        now = datetime.utcnow()
        lease_expires_at = now + lease_duration
        
        # Build the subquery to select jobs
        # Uses FOR UPDATE SKIP LOCKED to prevent contention
        subquery_filters = [
            Job.status == JobStatus.QUEUED,
            or_(Job.scheduled_at <= now, Job.scheduled_at.is_(None)),
        ]
        
        if tenant_id is not None:
            subquery_filters.append(Job.tenant_id == tenant_id)
        
        # Use raw SQL for the atomic update with SKIP LOCKED
        # This ensures we don't double-lease jobs
        sql = text("""
            UPDATE jobs
            SET 
                lease_owner = :worker_id,
                lease_expires_at = :lease_expires_at,
                status = :leased_status,
                updated_at = :now
            WHERE id IN (
                SELECT id FROM jobs
                WHERE status = :queued_status
                AND (scheduled_at <= :now OR scheduled_at IS NULL)
                ORDER BY 
                    CASE priority 
                        WHEN 'critical' THEN 100
                        WHEN 'high' THEN 10
                        WHEN 'normal' THEN 5
                        WHEN 'low' THEN 1
                    END DESC,
                    created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            RETURNING *
        """)
        
        result = await self._session.execute(
            sql,
            {
                "worker_id": worker_id,
                "lease_expires_at": lease_expires_at,
                "leased_status": JobStatus.LEASED.value,
                "queued_status": JobStatus.QUEUED.value,
                "now": now,
                "batch_size": batch_size,
            }
        )
        
        rows = result.fetchall()
        
        if rows:
            logger.info(
                f"Acquired lease on {len(rows)} jobs",
                extra={"worker_id": worker_id, "job_count": len(rows)}
            )
        
        # Convert rows to Job objects
        jobs = []
        for row in rows:
            job = Job(
                id=row.id,
                tenant_id=row.tenant_id,
                idempotency_key=row.idempotency_key,
                payload=row.payload,
                status=JobStatus(row.status),
                priority=JobPriority(row.priority),
                attempt=row.attempt,
                max_attempts=row.max_attempts,
                lease_owner=row.lease_owner,
                lease_expires_at=row.lease_expires_at,
                scheduled_at=row.scheduled_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
                completed_at=row.completed_at,
                last_error=row.last_error,
                result=row.result,
            )
            jobs.append(job)
        
        return jobs

    async def check_tenant_concurrency(
        self,
        tenant_id: str,
        max_concurrent: int,
    ) -> bool:
        """
        Check if tenant has capacity for more concurrent jobs.
        
        Args:
            tenant_id: The tenant identifier.
            max_concurrent: Maximum concurrent jobs allowed.
            
        Returns:
            True if tenant has capacity, False otherwise.
        """
        stmt = select(func.count()).select_from(Job).where(
            and_(
                Job.tenant_id == tenant_id,
                Job.status.in_([JobStatus.LEASED, JobStatus.RUNNING]),
            )
        )
        result = await self._session.execute(stmt)
        current = result.scalar() or 0
        
        return current < max_concurrent

    async def start_job(self, job_id: UUID, worker_id: str) -> Job | None:
        """
        Transition job from LEASED to RUNNING.
        
        Args:
            job_id: The job UUID.
            worker_id: The worker identifier (must match lease owner).
            
        Returns:
            Updated Job or None if transition failed.
        """
        stmt = (
            update(Job)
            .where(
                and_(
                    Job.id == job_id,
                    Job.status == JobStatus.LEASED,
                    Job.lease_owner == worker_id,
                )
            )
            .values(
                status=JobStatus.RUNNING,
                attempt=Job.attempt + 1,
                updated_at=datetime.utcnow(),
            )
            .returning(Job)
        )
        
        result = await self._session.execute(stmt)
        job = result.scalar_one_or_none()
        
        if job:
            logger.info(
                f"Started job execution",
                extra={"job_id": str(job_id), "attempt": job.attempt}
            )
        
        return job

    async def complete_job(
        self,
        job_id: UUID,
        worker_id: str,
        result: dict | None = None,
    ) -> Job | None:
        """
        Mark job as successfully completed.
        
        Args:
            job_id: The job UUID.
            worker_id: The worker identifier.
            result: Optional job result data.
            
        Returns:
            Updated Job or None if transition failed.
        """
        now = datetime.utcnow()
        stmt = (
            update(Job)
            .where(
                and_(
                    Job.id == job_id,
                    Job.status == JobStatus.RUNNING,
                    Job.lease_owner == worker_id,
                )
            )
            .values(
                status=JobStatus.SUCCEEDED,
                completed_at=now,
                updated_at=now,
                lease_owner=None,
                lease_expires_at=None,
                result=result,
            )
            .returning(Job)
        )
        
        result_obj = await self._session.execute(stmt)
        job = result_obj.scalar_one_or_none()
        
        if job:
            logger.info(
                f"Job completed successfully",
                extra={"job_id": str(job_id)}
            )
        
        return job

    async def fail_job(
        self,
        job_id: UUID,
        worker_id: str,
        error: str,
    ) -> Job | None:
        """
        Handle job failure. Either retry or move to DLQ.
        
        Args:
            job_id: The job UUID.
            worker_id: The worker identifier.
            error: Error message.
            
        Returns:
            Updated Job or None if transition failed.
        """
        # First, get the job to check attempt count
        job = await self.get_job(job_id)
        if job is None:
            return None
        
        if job.lease_owner != worker_id:
            logger.warning(
                "Worker doesn't own job lease",
                extra={"job_id": str(job_id), "worker_id": worker_id}
            )
            return None
        
        now = datetime.utcnow()
        
        # Determine next status
        if job.attempt >= job.max_attempts:
            # Max attempts reached, move to DLQ
            new_status = JobStatus.DLQ
            completed_at = now
            logger.warning(
                f"Job moved to DLQ after {job.attempt} attempts",
                extra={"job_id": str(job_id), "error": error}
            )
        else:
            # Retry the job
            new_status = JobStatus.QUEUED
            completed_at = None
            logger.info(
                f"Job queued for retry",
                extra={"job_id": str(job_id), "attempt": job.attempt}
            )
        
        stmt = (
            update(Job)
            .where(
                and_(
                    Job.id == job_id,
                    Job.status == JobStatus.RUNNING,
                    Job.lease_owner == worker_id,
                )
            )
            .values(
                status=new_status,
                last_error=error,
                updated_at=now,
                completed_at=completed_at,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(Job)
        )
        
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def retry_from_dlq(
        self,
        job_id: UUID,
        reset_attempts: bool = True,
    ) -> Job | None:
        """
        Retry a job from the dead letter queue.
        
        Args:
            job_id: The job UUID.
            reset_attempts: Whether to reset the attempt counter.
            
        Returns:
            Updated Job or None if not found or not in DLQ.
        """
        now = datetime.utcnow()
        values = {
            "status": JobStatus.QUEUED,
            "updated_at": now,
            "completed_at": None,
            "last_error": None,
        }
        
        if reset_attempts:
            values["attempt"] = 0
        
        stmt = (
            update(Job)
            .where(
                and_(
                    Job.id == job_id,
                    Job.status == JobStatus.DLQ,
                )
            )
            .values(**values)
            .returning(Job)
        )
        
        result = await self._session.execute(stmt)
        job = result.scalar_one_or_none()
        
        if job:
            logger.info(
                f"Job retried from DLQ",
                extra={"job_id": str(job_id)}
            )
        
        return job

    async def recover_expired_leases(self) -> int:
        """
        Recover jobs with expired leases.
        
        This is called by the reaper to handle worker crashes.
        Jobs in LEASED status with expired leases are returned to QUEUED.
        
        Returns:
            Number of recovered jobs.
        """
        now = datetime.utcnow()
        
        stmt = (
            update(Job)
            .where(
                and_(
                    Job.status == JobStatus.LEASED,
                    Job.lease_expires_at < now,
                )
            )
            .values(
                status=JobStatus.QUEUED,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        
        result = await self._session.execute(stmt)
        count = result.rowcount
        
        if count > 0:
            logger.info(
                f"Recovered {count} jobs with expired leases"
            )
        
        return count

    async def extend_lease(
        self,
        job_id: UUID,
        worker_id: str,
        extension_seconds: int | None = None,
    ) -> bool:
        """
        Extend the lease on a job (heartbeat).
        
        Args:
            job_id: The job UUID.
            worker_id: The worker identifier.
            extension_seconds: Lease extension duration.
            
        Returns:
            True if lease was extended, False otherwise.
        """
        if extension_seconds is None:
            extension_seconds = self._settings.worker_lease_duration_seconds
        
        now = datetime.utcnow()
        new_expires_at = now + timedelta(seconds=extension_seconds)
        
        stmt = (
            update(Job)
            .where(
                and_(
                    Job.id == job_id,
                    Job.lease_owner == worker_id,
                    Job.status.in_([JobStatus.LEASED, JobStatus.RUNNING]),
                )
            )
            .values(
                lease_expires_at=new_expires_at,
                updated_at=now,
            )
        )
        
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def get_queue_depth(self, tenant_id: str | None = None) -> int:
        """
        Get the number of queued jobs.
        
        Args:
            tenant_id: Optional tenant filter.
            
        Returns:
            Number of queued jobs.
        """
        filters = [Job.status == JobStatus.QUEUED]
        if tenant_id is not None:
            filters.append(Job.tenant_id == tenant_id)
        
        stmt = select(func.count()).select_from(Job).where(and_(*filters))
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def get_job_stats(
        self,
        tenant_id: str | None = None,
    ) -> dict[str, int]:
        """
        Get job statistics by status.
        
        Args:
            tenant_id: Optional tenant filter.
            
        Returns:
            Dictionary of status -> count.
        """
        filters = []
        if tenant_id is not None:
            filters.append(Job.tenant_id == tenant_id)
        
        stmt = (
            select(Job.status, func.count())
            .group_by(Job.status)
        )
        if filters:
            stmt = stmt.where(and_(*filters))
        
        result = await self._session.execute(stmt)
        return {status.value: count for status, count in result.all()}
