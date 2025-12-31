"""
Worker process for executing jobs.

The worker pulls jobs from the queue, executes them, and handles
retries and failures according to the job lifecycle.
"""

import asyncio
import logging
import os
import signal
import time
from typing import Any
from uuid import UUID

from src.config import get_settings
from src.db import close_db, get_session_context, init_db
from src.db.repository import JobRepository
from src.observability.logging import setup_logging
from src.observability.metrics import get_metrics
from src.observability.tracing import get_tracer
from src.types.job import JobContext
from src.worker.handlers import execute_job

logger = logging.getLogger(__name__)


class Worker:
    """
    Job worker that polls for and executes jobs.
    
    Features:
    - Atomic lease acquisition using FOR UPDATE SKIP LOCKED
    - Heartbeat to extend leases for long-running jobs
    - Graceful shutdown on SIGTERM/SIGINT
    - Retry and DLQ handling
    """

    def __init__(
        self,
        worker_id: str | None = None,
        batch_size: int | None = None,
        poll_interval: float | None = None,
    ):
        """
        Initialize the worker.
        
        Args:
            worker_id: Unique worker identifier. Defaults to hostname + PID.
            batch_size: Number of jobs to acquire per poll.
            poll_interval: Seconds between polls when queue is empty.
        """
        settings = get_settings()

        self.worker_id = worker_id or f"{os.uname().nodename}-{os.getpid()}"
        self.batch_size = batch_size or settings.worker_batch_size
        self.poll_interval = poll_interval or settings.worker_poll_interval_seconds
        self.heartbeat_interval = settings.worker_heartbeat_interval_seconds

        self._running = False
        self._current_jobs: dict[UUID, asyncio.Task] = {}
        self._heartbeat_task: asyncio.Task | None = None
        self._metrics = get_metrics()

    async def start(self) -> None:
        """Start the worker."""
        logger.info(
            "Worker starting",
            extra={"worker_id": self.worker_id, "batch_size": self.batch_size}
        )

        self._running = True

        # Start heartbeat task
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Main polling loop
        while self._running:
            try:
                jobs_processed = await self._poll_and_execute()

                # If no jobs were processed, wait before polling again
                if jobs_processed == 0:
                    await asyncio.sleep(self.poll_interval)

            except Exception as e:
                logger.exception(
                    f"Error in worker loop: {e}",
                    extra={"worker_id": self.worker_id}
                )
                await asyncio.sleep(self.poll_interval)

        # Wait for current jobs to complete
        if self._current_jobs:
            logger.info(f"Waiting for {len(self._current_jobs)} jobs to complete")
            await asyncio.gather(*self._current_jobs.values(), return_exceptions=True)

        # Cancel heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        logger.info("Worker stopped", extra={"worker_id": self.worker_id})

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        logger.info("Worker stopping", extra={"worker_id": self.worker_id})
        self._running = False

    async def _poll_and_execute(self) -> int:
        """
        Poll for jobs and execute them.
        
        Returns:
            Number of jobs processed.
        """
        async with get_session_context() as session:
            repo = JobRepository(session)

            # Acquire leases on available jobs
            jobs = await repo.acquire_lease(
                worker_id=self.worker_id,
                batch_size=self.batch_size,
            )

            await session.commit()

            if not jobs:
                return 0

            # Record metrics
            self._metrics.record_lease_acquired(self.worker_id, len(jobs))

            logger.info(
                f"Acquired {len(jobs)} jobs",
                extra={"worker_id": self.worker_id}
            )

        # Execute jobs concurrently
        tasks = []
        for job in jobs:
            task = asyncio.create_task(self._execute_job(job))
            self._current_jobs[job.id] = task
            tasks.append(task)

        # Wait for all jobs to complete
        await asyncio.gather(*tasks, return_exceptions=True)

        return len(jobs)

    async def _execute_job(self, job: Any) -> None:
        """
        Execute a single job.
        
        Handles the full lifecycle:
        1. Transition to RUNNING
        2. Execute the handler
        3. Mark as SUCCEEDED or handle failure
        
        Args:
            job: The job to execute.
        """
        start_time = time.time()
        job_id = job.id

        try:
            async with get_session_context() as session:
                repo = JobRepository(session)

                # Transition to RUNNING
                running_job = await repo.start_job(job_id, self.worker_id)
                await session.commit()

                if running_job is None:
                    logger.warning(
                        "Failed to start job - lease may have expired",
                        extra={"job_id": str(job_id)}
                    )
                    return

            # Create job context
            context = JobContext(
                job_id=job_id,
                tenant_id=job.tenant_id,
                attempt=running_job.attempt,
                max_attempts=running_job.max_attempts,
                payload=job.payload,
                lease_owner=self.worker_id,
                lease_expires_at=job.lease_expires_at,
            )

            logger.info(
                "Executing job",
                extra={
                    "job_id": str(job_id),
                    "tenant_id": job.tenant_id,
                    "attempt": context.attempt,
                }
            )

            # Execute the job handler
            with get_tracer().start_as_current_span("execute_job") as span:
                span.set_attribute("job_id", str(job_id))
                span.set_attribute("tenant_id", job.tenant_id)
                span.set_attribute("attempt", context.attempt)

                result = await execute_job(context)

            # Handle result
            async with get_session_context() as session:
                repo = JobRepository(session)

                duration = time.time() - start_time

                if result.success:
                    # Mark as succeeded
                    await repo.complete_job(
                        job_id=job_id,
                        worker_id=self.worker_id,
                        result=result.output,
                    )

                    logger.info(
                        "Job completed successfully",
                        extra={
                            "job_id": str(job_id),
                            "duration": f"{duration:.2f}s",
                        }
                    )

                    self._metrics.record_job_completed(
                        tenant_id=job.tenant_id,
                        status="succeeded",
                        duration_seconds=duration,
                    )
                else:
                    # Mark as failed (may retry or go to DLQ)
                    await repo.fail_job(
                        job_id=job_id,
                        worker_id=self.worker_id,
                        error=result.error or "Unknown error",
                    )

                    logger.warning(
                        "Job failed",
                        extra={
                            "job_id": str(job_id),
                            "error": result.error,
                            "attempt": context.attempt,
                        }
                    )

                    # Determine final status for metrics
                    updated_job = await repo.get_job(job_id)
                    final_status = updated_job.status.value if updated_job else "failed"

                    self._metrics.record_job_completed(
                        tenant_id=job.tenant_id,
                        status=final_status,
                        duration_seconds=duration,
                    )

                await session.commit()

        except Exception as e:
            logger.exception(
                "Exception executing job",
                extra={"job_id": str(job_id), "error": str(e)}
            )

            # Try to mark job as failed
            try:
                async with get_session_context() as session:
                    repo = JobRepository(session)
                    await repo.fail_job(
                        job_id=job_id,
                        worker_id=self.worker_id,
                        error=f"Worker exception: {str(e)}",
                    )
                    await session.commit()
            except Exception:
                logger.exception("Failed to mark job as failed")

        finally:
            # Remove from current jobs
            self._current_jobs.pop(job_id, None)

    async def _heartbeat_loop(self) -> None:
        """
        Periodically extend leases on running jobs.
        
        This prevents jobs from being reclaimed by the reaper
        while they're still being executed.
        """
        while self._running:
            try:
                await asyncio.sleep(self.heartbeat_interval)

                if not self._current_jobs:
                    continue

                async with get_session_context() as session:
                    repo = JobRepository(session)

                    for job_id in list(self._current_jobs.keys()):
                        extended = await repo.extend_lease(job_id, self.worker_id)
                        if extended:
                            logger.debug(
                                "Extended lease",
                                extra={"job_id": str(job_id)}
                            )

                    await session.commit()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in heartbeat loop: {e}")


async def run_async() -> None:
    """Run the worker asynchronously."""
    setup_logging()
    await init_db()

    worker = Worker()

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(worker.stop())
        )

    try:
        await worker.start()
    finally:
        await close_db()


def run() -> None:
    """Run the worker."""
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
