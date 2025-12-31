"""
Health check routes.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_async_session
from src.observability.metrics import get_metrics
from src.types.api import HealthResponse

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Check the health of the API and database connection.",
)
async def health_check(
    session: AsyncSession = Depends(get_async_session),
) -> HealthResponse:
    """
    Perform a health check.

    Checks database connectivity and returns service status.

    Args:
        session: Database session.

    Returns:
        HealthResponse with service status.
    """
    # Check database
    db_status = "healthy"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "unhealthy"

    return HealthResponse(
        status="healthy" if db_status == "healthy" else "degraded",
        version="1.0.0",
        database=db_status,
        timestamp=datetime.utcnow(),
    )


@router.get(
    "/ready",
    summary="Readiness check",
    description="Check if the service is ready to receive traffic.",
)
async def readiness_check(
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Kubernetes readiness probe endpoint.

    Args:
        session: Database session.

    Returns:
        Ready status.
    """
    try:
        await session.execute(text("SELECT 1"))
        return {"ready": True}
    except Exception:
        return {"ready": False}


@router.get(
    "/live",
    summary="Liveness check",
    description="Check if the service is alive.",
)
async def liveness_check() -> dict:
    """
    Kubernetes liveness probe endpoint.

    Returns:
        Alive status.
    """
    return {"alive": True}


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    description="Expose Prometheus metrics.",
)
async def metrics() -> Response:
    """
    Expose Prometheus metrics.

    Returns:
        Prometheus-formatted metrics.
    """
    metrics_collector = get_metrics()
    return Response(
        content=metrics_collector.get_metrics(),
        media_type=metrics_collector.get_content_type(),
    )
