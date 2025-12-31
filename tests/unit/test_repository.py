"""
Unit tests for the job repository.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import JobPriority, JobStatus
from src.db.repository import JobRepository


class TestJobRepository:
    """Tests for JobRepository."""

    @pytest_asyncio.fixture
    async def repo(self, db_session: AsyncSession) -> JobRepository:
        """Create a repository instance."""
        return JobRepository(db_session)

    async def test_create_job_success(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test successful job creation."""
        tenant_id = "test-tenant"
        idempotency_key = f"test-{uuid4().hex}"
        payload = {"job_type": "echo", "data": {"message": "test"}}

        job, created = await repo.create_job(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            payload=payload,
            max_attempts=3,
            priority=JobPriority.NORMAL,
        )
        await db_session.commit()

        assert created is True
        assert job is not None
        assert job.tenant_id == tenant_id
        assert job.idempotency_key == idempotency_key
        assert job.payload == payload
        assert job.status == JobStatus.QUEUED
        assert job.attempt == 0
        assert job.max_attempts == 3

    async def test_create_job_idempotency(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test that duplicate idempotency keys return existing job."""
        tenant_id = "test-tenant"
        idempotency_key = f"test-{uuid4().hex}"
        payload = {"job_type": "echo", "data": {"message": "test"}}

        # Create first job
        job1, created1 = await repo.create_job(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        await db_session.commit()

        # Try to create with same idempotency key
        job2, created2 = await repo.create_job(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            payload={"different": "payload"},
        )

        assert created1 is True
        assert created2 is False
        assert job1.id == job2.id

    async def test_create_job_different_tenants_same_key(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test that same idempotency key works for different tenants."""
        idempotency_key = f"test-{uuid4().hex}"
        payload = {"job_type": "echo", "data": {}}

        job1, created1 = await repo.create_job(
            tenant_id="tenant-1",
            idempotency_key=idempotency_key,
            payload=payload,
        )
        await db_session.commit()

        job2, created2 = await repo.create_job(
            tenant_id="tenant-2",
            idempotency_key=idempotency_key,
            payload=payload,
        )
        await db_session.commit()

        assert created1 is True
        assert created2 is True
        assert job1.id != job2.id

    async def test_get_job_by_id(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test getting a job by ID."""
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"test-{uuid4().hex}",
            payload={"job_type": "echo"},
        )
        await db_session.commit()

        retrieved = await repo.get_job(job.id)

        assert retrieved is not None
        assert retrieved.id == job.id

    async def test_get_job_not_found(self, repo: JobRepository):
        """Test getting a non-existent job."""
        job = await repo.get_job(uuid4())
        assert job is None

    async def test_acquire_lease_success(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test successful lease acquisition."""
        # Create a queued job
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"test-{uuid4().hex}",
            payload={"job_type": "echo"},
        )
        await db_session.commit()

        # Acquire lease
        worker_id = "test-worker"
        jobs = await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await db_session.commit()

        assert len(jobs) == 1
        leased_job = jobs[0]
        assert leased_job.id == job.id
        assert leased_job.status == JobStatus.LEASED
        assert leased_job.lease_owner == worker_id
        assert leased_job.lease_expires_at is not None

    async def test_acquire_lease_skip_locked(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test that FOR UPDATE SKIP LOCKED prevents double-leasing."""
        # Create multiple queued jobs
        for i in range(3):
            await repo.create_job(
                tenant_id="test-tenant",
                idempotency_key=f"test-{uuid4().hex}",
                payload={"job_type": "echo"},
            )
        await db_session.commit()

        # First worker acquires leases
        jobs1 = await repo.acquire_lease(worker_id="worker-1", batch_size=2)
        await db_session.commit()

        # Second worker should get remaining jobs
        jobs2 = await repo.acquire_lease(worker_id="worker-2", batch_size=2)
        await db_session.commit()

        assert len(jobs1) == 2
        assert len(jobs2) == 1

        # No overlap in job IDs
        job_ids_1 = {j.id for j in jobs1}
        job_ids_2 = {j.id for j in jobs2}
        assert job_ids_1.isdisjoint(job_ids_2)

    async def test_complete_job_success(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test successful job completion."""
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"test-{uuid4().hex}",
            payload={"job_type": "echo"},
        )
        await db_session.commit()

        # Acquire and start
        worker_id = "test-worker"
        await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await db_session.commit()

        started = await repo.start_job(job.id, worker_id)
        await db_session.commit()
        assert started is not None

        # Complete
        result = {"output": "success"}
        completed = await repo.complete_job(job.id, worker_id, result=result)
        await db_session.commit()

        assert completed is not None
        assert completed.status == JobStatus.SUCCEEDED
        assert completed.result == result
        assert completed.completed_at is not None
        assert completed.lease_owner is None

    async def test_fail_job_with_retry(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test job failure with retry available."""
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"test-{uuid4().hex}",
            payload={"job_type": "echo"},
            max_attempts=3,
        )
        await db_session.commit()

        worker_id = "test-worker"
        await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await repo.start_job(job.id, worker_id)
        await db_session.commit()

        failed = await repo.fail_job(job.id, worker_id, error="Test error")
        await db_session.commit()

        assert failed is not None
        assert failed.status == JobStatus.QUEUED  # Back to queue for retry
        assert failed.attempt == 1
        assert failed.last_error == "Test error"

    async def test_fail_job_to_dlq(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test job failure moves to DLQ after max attempts."""
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"test-{uuid4().hex}",
            payload={"job_type": "echo"},
            max_attempts=1,
        )
        await db_session.commit()

        worker_id = "test-worker"

        # First attempt
        await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await repo.start_job(job.id, worker_id)
        await db_session.commit()

        # Fail - should go to DLQ since max_attempts=1
        failed = await repo.fail_job(job.id, worker_id, error="Final error")
        await db_session.commit()

        assert failed is not None
        assert failed.status == JobStatus.DLQ
        assert failed.attempt == 1

    async def test_recover_expired_leases(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test recovery of expired leases."""
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"test-{uuid4().hex}",
            payload={"job_type": "echo"},
        )
        await db_session.commit()

        # Acquire lease
        await repo.acquire_lease(worker_id="test-worker", batch_size=1)
        await db_session.commit()

        # Manually expire the lease by updating lease_expires_at
        from sqlalchemy import update

        from src.db.models import Job

        await db_session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(lease_expires_at=datetime.utcnow() - timedelta(minutes=1))
        )
        await db_session.commit()

        # Recover
        recovered_count = await repo.recover_expired_leases()
        await db_session.commit()

        assert recovered_count == 1

        # Verify job is back in queue
        updated_job = await repo.get_job(job.id)
        assert updated_job.status == JobStatus.QUEUED
        assert updated_job.lease_owner is None

    async def test_retry_from_dlq(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test retrying a job from DLQ."""
        job, _ = await repo.create_job(
            tenant_id="test-tenant",
            idempotency_key=f"test-{uuid4().hex}",
            payload={"job_type": "echo"},
            max_attempts=1,
        )
        await db_session.commit()

        # Move to DLQ
        worker_id = "test-worker"
        await repo.acquire_lease(worker_id=worker_id, batch_size=1)
        await repo.start_job(job.id, worker_id)
        await repo.fail_job(job.id, worker_id, error="Error")
        await db_session.commit()

        # Retry from DLQ
        retried = await repo.retry_from_dlq(job.id, reset_attempts=True)
        await db_session.commit()

        assert retried is not None
        assert retried.status == JobStatus.QUEUED
        assert retried.attempt == 0

    async def test_check_tenant_concurrency(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test tenant concurrency check."""
        tenant_id = "test-tenant"

        # No active jobs - should have capacity
        can_accept = await repo.check_tenant_concurrency(tenant_id, max_concurrent=2)
        assert can_accept is True

        # Create and lease jobs
        for i in range(2):
            await repo.create_job(
                tenant_id=tenant_id,
                idempotency_key=f"test-{uuid4().hex}",
                payload={"job_type": "echo"},
            )
        await db_session.commit()

        await repo.acquire_lease(worker_id="worker", batch_size=2)
        await db_session.commit()

        # Now at capacity
        can_accept = await repo.check_tenant_concurrency(tenant_id, max_concurrent=2)
        assert can_accept is False

    async def test_priority_ordering(
        self,
        repo: JobRepository,
        db_session: AsyncSession,
    ):
        """Test that jobs are leased in priority order."""
        tenant_id = f"test-tenant-{uuid4().hex[:8]}"

        # Create jobs with different priorities
        low, _ = await repo.create_job(
            tenant_id=tenant_id,
            idempotency_key=f"low-{uuid4().hex}",
            payload={"job_type": "echo"},
            priority=JobPriority.LOW,
        )
        high, _ = await repo.create_job(
            tenant_id=tenant_id,
            idempotency_key=f"high-{uuid4().hex}",
            payload={"job_type": "echo"},
            priority=JobPriority.HIGH,
        )
        critical, _ = await repo.create_job(
            tenant_id=tenant_id,
            idempotency_key=f"critical-{uuid4().hex}",
            payload={"job_type": "echo"},
            priority=JobPriority.CRITICAL,
        )
        await db_session.commit()

        # Acquire one at a time and verify order
        jobs = await repo.acquire_lease(worker_id="worker", batch_size=3)
        await db_session.commit()

        job_ids = [j.id for j in jobs]
        assert job_ids[0] == critical.id
        assert job_ids[1] == high.id
        assert job_ids[2] == low.id
