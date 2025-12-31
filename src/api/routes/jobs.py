"""
Job management routes.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import CurrentUser
from src.api.websocket import get_ws_manager
from src.config import get_settings
from src.constants import IDEMPOTENCY_KEY_HEADER, JobStatus
from src.db import get_async_session
from src.db.repository import JobRepository
from src.observability.metrics import get_metrics
from src.types.api import (
    CreateJobRequest,
    CreateJobResponse,
    JobListResponse,
    JobResponse,
    RetryJobRequest,
    RetryJobResponse,
)
from src.types.events import JobEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/jobs", tags=["Jobs"])


def _job_to_response(job) -> JobResponse:
    """Convert a Job model to a JobResponse."""
    return JobResponse(
        id=job.id,
        tenant_id=job.tenant_id,
        idempotency_key=job.idempotency_key,
        payload=job.payload,
        status=job.status,
        priority=job.priority,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        lease_owner=job.lease_owner,
        lease_expires_at=job.lease_expires_at,
        scheduled_at=job.scheduled_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
        last_error=job.last_error,
    )


@router.post(
    "",
    response_model=CreateJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a job",
    description="Submit a new job to the queue. Uses idempotency key to prevent duplicates.",
)
async def create_job(
    request: CreateJobRequest,
    current_user: CurrentUser,
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
    session: AsyncSession = Depends(get_async_session),
) -> CreateJobResponse:
    """
    Create a new job.
    
    Submission is idempotent based on (tenant_id, idempotency_key).
    If a job with the same key exists, returns the existing job.
    
    Args:
        request: Job creation request.
        current_user: Authenticated user context.
        idempotency_key: Unique key for idempotent submission.
        session: Database session.
        
    Returns:
        CreateJobResponse with job details.
    """
    settings = get_settings()
    repo = JobRepository(session)

    # Check tenant concurrency limits before accepting job
    can_accept = await repo.check_tenant_concurrency(
        tenant_id=current_user.tenant_id,
        max_concurrent=settings.default_tenant_max_concurrent_jobs,
    )

    if not can_accept:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Tenant concurrency limit exceeded",
        )

    # Create the job
    job, created = await repo.create_job(
        tenant_id=current_user.tenant_id,
        idempotency_key=idempotency_key,
        payload=request.payload,
        max_attempts=request.max_attempts,
        priority=request.priority,
        scheduled_at=request.scheduled_at,
    )

    await session.commit()

    # Record metrics
    if created:
        metrics = get_metrics()
        metrics.record_job_submitted(
            tenant_id=current_user.tenant_id,
            priority=request.priority.value,
        )

        # Broadcast WebSocket event
        ws_manager = get_ws_manager()
        event = JobEvent.job_created(
            job_id=job.id,
            tenant_id=job.tenant_id,
            payload=job.payload,
        )
        await ws_manager.broadcast_job_event(event)

    return CreateJobResponse(
        id=job.id,
        tenant_id=job.tenant_id,
        idempotency_key=job.idempotency_key,
        status=job.status,
        created_at=job.created_at,
        message="Job created successfully" if created else "Job already exists (idempotent)",
    )


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get job details",
    description="Get detailed information about a specific job.",
)
async def get_job(
    job_id: UUID,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
) -> JobResponse:
    """
    Get job details by ID.
    
    Args:
        job_id: The job UUID.
        current_user: Authenticated user context.
        session: Database session.
        
    Returns:
        JobResponse with full job details.
        
    Raises:
        HTTPException: If job not found or not owned by tenant.
    """
    repo = JobRepository(session)
    job = await repo.get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )

    # Ensure tenant owns this job
    if job.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    return _job_to_response(job)


@router.get(
    "",
    response_model=JobListResponse,
    summary="List jobs",
    description="List jobs for the authenticated tenant with optional filtering.",
)
async def list_jobs(
    current_user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: JobStatus | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> JobListResponse:
    """
    List jobs for the current tenant.
    
    Args:
        current_user: Authenticated user context.
        page: Page number (1-indexed).
        page_size: Number of items per page.
        status: Optional status filter.
        session: Database session.
        
    Returns:
        JobListResponse with paginated jobs.
    """
    repo = JobRepository(session)
    offset = (page - 1) * page_size

    jobs, total = await repo.list_jobs(
        tenant_id=current_user.tenant_id,
        status=status,
        limit=page_size,
        offset=offset,
    )

    return JobListResponse(
        jobs=[_job_to_response(job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
        has_next=(page * page_size) < total,
    )


@router.post(
    "/{job_id}/retry",
    response_model=RetryJobResponse,
    summary="Retry a job from DLQ",
    description="Retry a job that has been moved to the dead letter queue.",
)
async def retry_job(
    job_id: UUID,
    current_user: CurrentUser,
    request: RetryJobRequest = RetryJobRequest(),
    session: AsyncSession = Depends(get_async_session),
) -> RetryJobResponse:
    """
    Retry a job from the DLQ.
    
    Args:
        job_id: The job UUID.
        current_user: Authenticated user context.
        request: Retry request options.
        session: Database session.
        
    Returns:
        RetryJobResponse with updated job info.
        
    Raises:
        HTTPException: If job not found or not in DLQ.
    """
    repo = JobRepository(session)

    # Get the job first
    job = await repo.get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )

    if job.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if job.status != JobStatus.DLQ:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is not in DLQ (current status: {job.status})",
        )

    # Retry the job
    updated_job = await repo.retry_from_dlq(
        job_id=job_id,
        reset_attempts=request.reset_attempts,
    )

    await session.commit()

    if updated_job is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retry job",
        )

    logger.info(
        "Job retried from DLQ",
        extra={"job_id": str(job_id), "tenant_id": current_user.tenant_id}
    )

    return RetryJobResponse(
        id=updated_job.id,
        status=updated_job.status,
        attempt=updated_job.attempt,
        message="Job queued for retry",
    )


@router.get(
    "/stats/summary",
    summary="Get job statistics",
    description="Get job statistics by status for the tenant.",
)
async def get_job_stats(
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Get job statistics for the current tenant.
    
    Args:
        current_user: Authenticated user context.
        session: Database session.
        
    Returns:
        Dictionary of status -> count.
    """
    repo = JobRepository(session)
    stats = await repo.get_job_stats(tenant_id=current_user.tenant_id)
    queue_depth = await repo.get_queue_depth(tenant_id=current_user.tenant_id)

    return {
        "stats": stats,
        "queue_depth": queue_depth,
    }
