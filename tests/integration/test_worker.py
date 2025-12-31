"""
Integration tests for worker functionality.
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import JobPriority, JobStatus
from src.db.repository import JobRepository
from src.types.job import JobContext
from src.worker.handlers import execute_job


class TestWorkerIntegration:
    """Integration tests for worker job processing."""

    @pytest_asyncio.fixture
    async def repo(self, db_session: AsyncSession) -> JobRepository:
        """Create a repository instance."""
        return JobRepository(db_session)

    @pytest.mark.asyncio
    async def test_full_job_lifecycle_success(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test complete job lifecycle: create -> lease -> run -> complete."""
        # Create job
        job, created = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"lifecycle-{uuid4().hex}",
            payload={"job_type": "echo", "data": {"message": "test"}},
            max_attempts=3,
        )
        await db_session.commit()
        assert created is True
        assert job.status == JobStatus.QUEUED

        # Acquire lease
        worker_id = "test-worker"
        jobs = await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await db_session.commit()
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.LEASED

        # Start job
        started = await repo.start_job(job.id, worker_id)
        await db_session.commit()
        assert started is not None
        assert started.status == JobStatus.RUNNING
        assert started.attempt == 1

        # Execute job handler
        context = JobContext(
            job_id=job.id,
            tenant_id=job.tenant_id,
            attempt=started.attempt,
            max_attempts=started.max_attempts,
            payload=job.payload,
            lease_owner=worker_id,
            lease_expires_at=jobs[0].lease_expires_at,
        )
        result = await execute_job(context)
        assert result.success is True

        # Complete job
        completed = await repo.complete_job(
            job_id=job.id,
            worker_id=worker_id,
            result=result.output,
        )
        await db_session.commit()
        assert completed is not None
        assert completed.status == JobStatus.SUCCEEDED
        assert completed.completed_at is not None

    @pytest.mark.asyncio
    async def test_job_retry_on_failure(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test job is retried after failure."""
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"retry-{uuid4().hex}",
            payload={"job_type": "failing_job"},
            max_attempts=3,
        )
        await db_session.commit()

        worker_id = "test-worker"

        # First attempt
        await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await repo.start_job(job.id, worker_id)
        await db_session.commit()

        failed = await repo.fail_job(job.id, worker_id, error="First failure")
        await db_session.commit()

        assert failed.status == JobStatus.QUEUED
        assert failed.attempt == 1

        # Second attempt
        jobs = await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await db_session.commit()
        assert len(jobs) == 1

        started = await repo.start_job(job.id, worker_id)
        await db_session.commit()
        assert started.attempt == 2

    @pytest.mark.asyncio
    async def test_job_moves_to_dlq_after_max_attempts(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test job moves to DLQ after exhausting retries."""
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"dlq-{uuid4().hex}",
            payload={"job_type": "failing_job"},
            max_attempts=2,
        )
        await db_session.commit()

        worker_id = "test-worker"

        # First attempt
        await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await repo.start_job(job.id, worker_id)
        await repo.fail_job(job.id, worker_id, error="Failure 1")
        await db_session.commit()

        # Second attempt (last)
        await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await repo.start_job(job.id, worker_id)
        await db_session.commit()

        failed = await repo.fail_job(job.id, worker_id, error="Failure 2")
        await db_session.commit()

        assert failed.status == JobStatus.DLQ
        assert failed.attempt == 2

    @pytest.mark.asyncio
    async def test_lease_expiry_recovery(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test that expired leases are recovered by reaper."""
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"expiry-{uuid4().hex}",
            payload={"job_type": "echo"},
        )
        await db_session.commit()

        # Acquire lease
        worker_id = "crashed-worker"
        jobs = await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await db_session.commit()
        assert len(jobs) == 1

        # Simulate worker crash by expiring the lease
        from sqlalchemy import update
        from src.db.models import Job

        await db_session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(lease_expires_at=datetime.utcnow() - timedelta(minutes=5))
        )
        await db_session.commit()

        # Reaper recovers the job
        recovered = await repo.recover_expired_leases()
        await db_session.commit()
        assert recovered == 1

        # Job should be available for another worker
        updated_job = await repo.get_job(job.id)
        assert updated_job.status == JobStatus.QUEUED
        assert updated_job.lease_owner is None

        # New worker can acquire it
        new_jobs = await repo.acquire_lease(worker_id="new-worker", batch_size=1)
        await db_session.commit()
        assert len(new_jobs) == 1
        assert new_jobs[0].id == job.id

    @pytest.mark.asyncio
    async def test_concurrent_workers_no_duplicate_execution(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test that multiple workers don't execute the same job."""
        # Create a single job
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"concurrent-{uuid4().hex}",
            payload={"job_type": "echo"},
        )
        await db_session.commit()

        # Simulate multiple workers trying to acquire
        jobs_worker1 = await repo.acquire_lease(worker_id="worker-1", batch_size=1)
        await db_session.commit()

        jobs_worker2 = await repo.acquire_lease(worker_id="worker-2", batch_size=1)
        await db_session.commit()

        # Only one worker should get the job
        assert len(jobs_worker1) + len(jobs_worker2) == 1

    @pytest.mark.asyncio
    async def test_tenant_concurrency_limit(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test per-tenant concurrency limits are enforced."""
        tenant_id = "limited-tenant"
        max_concurrent = 2

        # Create multiple jobs
        for i in range(5):
            await repo.create_job(
                tenant_id=tenant_id,
                idempotency_key=f"limit-{uuid4().hex}",
                payload={"job_type": "sleep", "data": {"duration_seconds": 10}},
            )
        await db_session.commit()

        # Acquire jobs up to limit
        await repo.acquire_lease(worker_id="worker", batch_size=2)
        await db_session.commit()

        # Check concurrency
        can_accept = await repo.check_tenant_concurrency(tenant_id, max_concurrent)
        assert can_accept is False

    @pytest.mark.asyncio
    async def test_retry_from_dlq_success(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test manually retrying a job from DLQ."""
        # Create job that will go to DLQ
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"dlq-retry-{uuid4().hex}",
            payload={"job_type": "echo"},
            max_attempts=1,
        )
        await db_session.commit()

        # Move to DLQ
        worker_id = "test-worker"
        await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await repo.start_job(job.id, worker_id)
        await repo.fail_job(job.id, worker_id, error="Failed")
        await db_session.commit()

        # Verify in DLQ
        dlq_job = await repo.get_job(job.id)
        assert dlq_job.status == JobStatus.DLQ

        # Retry from DLQ
        retried = await repo.retry_from_dlq(job.id, reset_attempts=True)
        await db_session.commit()

        assert retried.status == JobStatus.QUEUED
        assert retried.attempt == 0

        # Can be picked up again
        jobs = await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await db_session.commit()
        assert len(jobs) == 1
