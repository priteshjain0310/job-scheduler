"""
Job handlers registry and implementations.

Job handlers must be idempotent - they may be executed multiple times
for the same job in case of worker crashes or network issues.
"""

import asyncio
import logging
import random
from typing import Callable, Awaitable, Any

from src.types.job import JobContext, JobResult

logger = logging.getLogger(__name__)

# Type alias for job handler functions
JobHandler = Callable[[JobContext], Awaitable[JobResult]]

# Handler registry
_handlers: dict[str, JobHandler] = {}


def register_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    """
    Decorator to register a job handler.
    
    Args:
        job_type: The job type this handler processes.
        
    Returns:
        Decorator function.
        
    Example:
        @register_handler("send_email")
        async def handle_send_email(context: JobContext) -> JobResult:
            ...
    """
    def decorator(handler: JobHandler) -> JobHandler:
        _handlers[job_type] = handler
        logger.info(f"Registered handler for job type: {job_type}")
        return handler
    return decorator


def get_handler(job_type: str) -> JobHandler | None:
    """
    Get the handler for a job type.
    
    Args:
        job_type: The job type.
        
    Returns:
        The handler function or None if not found.
    """
    return _handlers.get(job_type)


def list_handlers() -> list[str]:
    """List all registered job types."""
    return list(_handlers.keys())


# ============================================================================
# Built-in job handlers
# ============================================================================


@register_handler("echo")
async def handle_echo(context: JobContext) -> JobResult:
    """
    Echo handler for testing.
    
    Simply returns the input payload as output.
    """
    logger.info(
        f"Echo job executing",
        extra={"job_id": str(context.job_id), "attempt": context.attempt}
    )
    
    return JobResult(
        success=True,
        output={"echo": context.payload},
    )


@register_handler("sleep")
async def handle_sleep(context: JobContext) -> JobResult:
    """
    Sleep handler for testing delays.
    
    Payload should contain:
    - duration_seconds: How long to sleep
    """
    duration = context.payload.get("data", {}).get("duration_seconds", 1)
    
    logger.info(
        f"Sleep job starting",
        extra={"job_id": str(context.job_id), "duration": duration}
    )
    
    await asyncio.sleep(duration)
    
    return JobResult(
        success=True,
        output={"slept_for": duration},
    )


@register_handler("failing_job")
async def handle_failing_job(context: JobContext) -> JobResult:
    """
    Handler that always fails - for testing retry logic.
    """
    logger.info(
        f"Failing job executing (will fail)",
        extra={"job_id": str(context.job_id), "attempt": context.attempt}
    )
    
    return JobResult(
        success=False,
        error=f"Intentional failure on attempt {context.attempt}",
    )


@register_handler("random_failure")
async def handle_random_failure(context: JobContext) -> JobResult:
    """
    Handler that randomly fails - for testing retry behavior.
    
    Payload should contain:
    - failure_rate: Probability of failure (0.0 to 1.0)
    """
    failure_rate = context.payload.get("data", {}).get("failure_rate", 0.5)
    
    if random.random() < failure_rate:
        logger.warning(
            f"Random failure triggered",
            extra={"job_id": str(context.job_id), "attempt": context.attempt}
        )
        return JobResult(
            success=False,
            error=f"Random failure on attempt {context.attempt}",
        )
    
    return JobResult(
        success=True,
        output={"message": "Succeeded this time!"},
    )


@register_handler("long_running")
async def handle_long_running(context: JobContext) -> JobResult:
    """
    Long running job for testing lease extension.
    
    Payload should contain:
    - duration_seconds: How long the job takes
    - checkpoint_interval: How often to log progress
    """
    duration = context.payload.get("data", {}).get("duration_seconds", 60)
    interval = context.payload.get("data", {}).get("checkpoint_interval", 5)
    
    elapsed = 0
    while elapsed < duration:
        await asyncio.sleep(min(interval, duration - elapsed))
        elapsed += interval
        
        logger.info(
            f"Long running job progress",
            extra={
                "job_id": str(context.job_id),
                "progress": f"{elapsed}/{duration}s",
            }
        )
    
    return JobResult(
        success=True,
        output={"duration": duration, "completed": True},
    )


@register_handler("http_request")
async def handle_http_request(context: JobContext) -> JobResult:
    """
    Make an HTTP request.
    
    Payload should contain:
    - url: The URL to request
    - method: HTTP method (GET, POST, etc.)
    - headers: Optional headers
    - body: Optional request body
    """
    import httpx
    
    data = context.payload.get("data", {})
    url = data.get("url")
    method = data.get("method", "GET").upper()
    headers = data.get("headers", {})
    body = data.get("body")
    
    if not url:
        return JobResult(
            success=False,
            error="Missing 'url' in payload",
        )
    
    logger.info(
        f"HTTP request job",
        extra={"job_id": str(context.job_id), "method": method, "url": url}
    )
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=body if method in ["POST", "PUT", "PATCH"] else None,
                timeout=30.0,
            )
            
            return JobResult(
                success=response.is_success,
                output={
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body": response.text[:1000],  # Truncate response
                },
                error=None if response.is_success else f"HTTP {response.status_code}",
            )
            
    except Exception as e:
        return JobResult(
            success=False,
            error=f"HTTP request failed: {str(e)}",
        )


async def execute_job(context: JobContext) -> JobResult:
    """
    Execute a job using the appropriate handler.
    
    Args:
        context: The job context.
        
    Returns:
        JobResult from the handler.
    """
    # Get job type from payload
    job_type = context.payload.get("job_type", "echo")
    
    # Find handler
    handler = get_handler(job_type)
    
    if handler is None:
        logger.error(
            f"No handler for job type: {job_type}",
            extra={"job_id": str(context.job_id)}
        )
        return JobResult(
            success=False,
            error=f"No handler registered for job type: {job_type}",
        )
    
    # Execute handler
    try:
        result = await handler(context)
        return result
    except Exception as e:
        logger.exception(
            f"Handler raised exception",
            extra={"job_id": str(context.job_id), "error": str(e)}
        )
        return JobResult(
            success=False,
            error=f"Handler exception: {str(e)}",
        )
