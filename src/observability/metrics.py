"""
Prometheus metrics collection.
"""

from typing import Any

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
    multiprocess,
    REGISTRY,
)

from src.constants import (
    METRIC_QUEUE_DEPTH,
    METRIC_JOBS_SUBMITTED,
    METRIC_JOBS_COMPLETED,
    METRIC_JOB_DURATION,
    METRIC_LEASE_EXPIRED,
    METRIC_LEASE_ACQUIRED,
    METRIC_API_REQUESTS,
    METRIC_API_LATENCY,
)

# Global metrics instance
_metrics: "MetricsCollector | None" = None


class MetricsCollector:
    """
    Prometheus metrics collector for the job scheduler.
    
    Collects metrics for:
    - Queue depth
    - Job submissions and completions
    - Job execution duration
    - Lease operations
    - API requests
    """

    def __init__(self, registry: CollectorRegistry | None = None):
        """
        Initialize the metrics collector.
        
        Args:
            registry: Optional custom registry. Uses default if not provided.
        """
        self._registry = registry or REGISTRY
        
        # Queue depth gauge (by tenant)
        self.queue_depth = Gauge(
            METRIC_QUEUE_DEPTH,
            "Number of jobs in the queue",
            ["tenant_id"],
            registry=self._registry,
        )
        
        # Jobs submitted counter
        self.jobs_submitted = Counter(
            METRIC_JOBS_SUBMITTED,
            "Total number of jobs submitted",
            ["tenant_id", "priority"],
            registry=self._registry,
        )
        
        # Jobs completed counter
        self.jobs_completed = Counter(
            METRIC_JOBS_COMPLETED,
            "Total number of jobs completed",
            ["tenant_id", "status"],
            registry=self._registry,
        )
        
        # Job duration histogram
        self.job_duration = Histogram(
            METRIC_JOB_DURATION,
            "Job execution duration in seconds",
            ["tenant_id", "status"],
            buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
            registry=self._registry,
        )
        
        # Lease expired counter
        self.lease_expired = Counter(
            METRIC_LEASE_EXPIRED,
            "Total number of expired leases",
            ["tenant_id"],
            registry=self._registry,
        )
        
        # Lease acquired counter
        self.lease_acquired = Counter(
            METRIC_LEASE_ACQUIRED,
            "Total number of leases acquired",
            ["worker_id"],
            registry=self._registry,
        )
        
        # API requests counter
        self.api_requests = Counter(
            METRIC_API_REQUESTS,
            "Total number of API requests",
            ["method", "endpoint", "status"],
            registry=self._registry,
        )
        
        # API latency histogram
        self.api_latency = Histogram(
            METRIC_API_LATENCY,
            "API request latency in seconds",
            ["method", "endpoint"],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
            registry=self._registry,
        )

    def record_job_submitted(self, tenant_id: str, priority: str) -> None:
        """Record a job submission."""
        self.jobs_submitted.labels(tenant_id=tenant_id, priority=priority).inc()

    def record_job_completed(
        self,
        tenant_id: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        """Record a job completion."""
        self.jobs_completed.labels(tenant_id=tenant_id, status=status).inc()
        self.job_duration.labels(tenant_id=tenant_id, status=status).observe(
            duration_seconds
        )

    def record_lease_expired(self, tenant_id: str) -> None:
        """Record an expired lease."""
        self.lease_expired.labels(tenant_id=tenant_id).inc()

    def record_lease_acquired(self, worker_id: str, count: int = 1) -> None:
        """Record lease acquisition."""
        self.lease_acquired.labels(worker_id=worker_id).inc(count)

    def update_queue_depth(self, tenant_id: str, depth: int) -> None:
        """Update queue depth for a tenant."""
        self.queue_depth.labels(tenant_id=tenant_id).set(depth)

    def record_api_request(
        self,
        method: str,
        endpoint: str,
        status: int,
        duration_seconds: float,
    ) -> None:
        """Record an API request."""
        self.api_requests.labels(
            method=method,
            endpoint=endpoint,
            status=str(status),
        ).inc()
        self.api_latency.labels(method=method, endpoint=endpoint).observe(
            duration_seconds
        )

    def get_metrics(self) -> bytes:
        """Get all metrics in Prometheus format."""
        return generate_latest(self._registry)

    def get_content_type(self) -> str:
        """Get the content type for metrics response."""
        return CONTENT_TYPE_LATEST


def setup_metrics() -> MetricsCollector:
    """
    Set up and return the metrics collector.
    
    Returns:
        MetricsCollector: The metrics collector instance.
    """
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


def get_metrics() -> MetricsCollector:
    """
    Get the metrics collector instance.
    
    Returns:
        MetricsCollector: The metrics collector instance.
        
    Raises:
        RuntimeError: If metrics are not set up.
    """
    if _metrics is None:
        return setup_metrics()
    return _metrics
