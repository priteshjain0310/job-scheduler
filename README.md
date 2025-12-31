# Distributed Job Queue

A **production-minded distributed job queue** demonstrating correct retry/lease/ack semantics, durable persistence, idempotency, per-tenant quotas, and observability.

**Focus: Correctness > Complexity**

---

## Table of Contents

1. [System Guarantees](#system-guarantees)
2. [Architecture](#architecture)
3. [Job Lifecycle](#job-lifecycle)
4. [Delivery Semantics](#delivery-semantics)
5. [Failure Scenarios](#failure-scenarios)
6. [Quotas & Fairness](#quotas--fairness)
7. [Observability](#observability)
8. [Scaling Approach](#scaling-approach)
9. [Testing Strategy](#testing-strategy)
10. [Quick Start](#quick-start)
11. [API Reference](#api-reference)
12. [Trade-offs & Future Improvements](#trade-offs--future-improvements)

---

## System Guarantees

| Guarantee | Description |
|-----------|-------------|
| **Submission** | Exactly-once (via idempotency key) |
| **Execution** | At-least-once |
| **No double execution while lease is valid** | FOR UPDATE SKIP LOCKED ensures only one worker processes a job at a time |
| **Jobs may re-execute after worker crash** | Expired leases are recovered by the reaper |

### What We Do NOT Claim

- **Exactly-once execution**: This is impossible in distributed systems without two-phase commit. Handlers must be idempotent.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENTS                                         │
│                    (HTTP API / WebSocket / Dashboard)                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           API SERVER (FastAPI)                               │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐│
│  │    Auth     │ │  Rate Limit │ │ Job Submit  │ │    WebSocket Hub        ││
│  │  (JWT/API)  │ │ (per-tenant)│ │ (idempotent)│ │  (live updates)         ││
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      POSTGRESQL (Single Source of Truth)                     │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                           jobs table                                     ││
│  │  • id, tenant_id, idempotency_key (UNIQUE constraint)                   ││
│  │  • payload, status, priority, attempt, max_attempts                     ││
│  │  • lease_owner, lease_expires_at                                        ││
│  │  • Partial indexes for efficient polling                                ││
│  └─────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
          ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
          │   WORKER 1  │   │   WORKER 2  │   │   WORKER N  │
          │  (stateless)│   │  (stateless)│   │  (stateless)│
          │             │   │             │   │             │
          │ • Poll jobs │   │ • Poll jobs │   │ • Poll jobs │
          │ • Lease     │   │ • Lease     │   │ • Lease     │
          │ • Execute   │   │ • Execute   │   │ • Execute   │
          │ • Ack/Retry │   │ • Ack/Retry │   │ • Ack/Retry │
          └─────────────┘   └─────────────┘   └─────────────┘
                                    │
                                    ▼
                          ┌─────────────────┐
                          │     REAPER      │
                          │                 │
                          │ • Lease expiry  │
                          │ • Re-enqueue    │
                          │ • Crash recover │
                          └─────────────────┘
```

### Components

| Component | Purpose |
|-----------|---------|
| **API Server** | FastAPI-based HTTP API with JWT auth, rate limiting, WebSocket support |
| **PostgreSQL** | Durable queue storage, job state, leases, WAL-backed crash recovery |
| **Workers** | Stateless job processors with lease-based execution |
| **Reaper** | Background process recovering expired leases (crash recovery) |
| **Dashboard** | Next.js real-time UI for job management |

---

## Job Lifecycle

```
                    ┌──────────────┐
                    │   SUBMITTED  │
                    │  (API call)  │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │    QUEUED    │◄─────────────────┐
                    │              │                  │
                    └──────┬───────┘                  │
                           │                          │
                           │ (lease acquired)         │ (retry)
                           ▼                          │
                    ┌──────────────┐                  │
                    │    LEASED    │──────────────────┤
                    │              │  (lease expired) │
                    └──────┬───────┘                  │
                           │                          │
                           │ (execution starts)       │
                           ▼                          │
                    ┌──────────────┐                  │
                    │   RUNNING    │──────────────────┘
                    │              │  (failure, retries left)
                    └──────┬───────┘
                           │
            ┌──────────────┼──────────────┐
            │              │              │
            ▼              │              ▼
     ┌──────────────┐      │       ┌──────────────┐
     │  SUCCEEDED   │      │       │     DLQ      │
     │              │      │       │              │
     └──────────────┘      │       └──────────────┘
                           │         (max attempts)
                           │
                    (failure, no retries)
```

### Status Definitions

| Status | Description |
|--------|-------------|
| `queued` | Job is waiting to be picked up by a worker |
| `leased` | Job has been claimed by a worker (lease acquired) |
| `running` | Job is actively being executed |
| `succeeded` | Job completed successfully |
| `failed` | Job failed (transient state before retry or DLQ) |
| `dlq` | Dead Letter Queue - job exhausted all retry attempts |

---

## Delivery Semantics

### Submission Idempotency

```sql
UNIQUE (tenant_id, idempotency_key)
```

- Each submission requires an `Idempotency-Key` header
- Duplicate submissions return the existing job
- Different tenants can use the same idempotency key

### Execution At-Least-Once

Jobs may execute multiple times in failure scenarios:

1. Worker crashes after starting execution but before acknowledging
2. Network partition prevents acknowledgment from reaching the database
3. Lease expires during execution

**Handlers MUST be idempotent** - design for safe re-execution.

### Lease-Based Coordination

```sql
-- Atomic lease acquisition with SKIP LOCKED
UPDATE jobs
SET lease_owner = :worker_id,
    lease_expires_at = now() + interval '30s',
    status = 'leased'
WHERE id IN (
  SELECT id FROM jobs
  WHERE status = 'queued'
    AND scheduled_at <= now()
  FOR UPDATE SKIP LOCKED
  LIMIT :batch_size
)
RETURNING *;
```

- `FOR UPDATE SKIP LOCKED` prevents contention between workers
- Lease duration is configurable (default 30s)
- Workers extend leases via heartbeat during long-running jobs

---

## Failure Scenarios

### Worker Crash During Execution

1. Worker acquires lease on job
2. Worker starts execution
3. **Worker crashes**
4. Lease expires (30s default)
5. Reaper detects expired lease
6. Job returns to `queued` status
7. Another worker picks up the job

### Database Unavailable

- Workers enter backoff loop
- Jobs remain in current state
- No data loss due to WAL durability
- Recovery is automatic when database returns

### Network Partition

- Workers may lose connection to database
- Leases prevent other workers from double-executing
- After partition heals:
  - If lease still valid: worker completes normally
  - If lease expired: job may re-execute (at-least-once)

### Poison Pill Jobs

- Jobs that always fail are retried up to `max_attempts`
- After exhausting retries, jobs move to DLQ
- DLQ jobs can be manually retried via API
- Error messages are preserved for debugging

---

## Quotas & Fairness

### API-Level Rate Limiting

```python
# Token bucket per tenant
rate_limit_requests_per_minute: 100
```

- In-memory rate limiting with burst capacity
- Returns 429 Too Many Requests when exceeded
- Configurable per deployment

### Execution-Level Concurrency Limits

```sql
-- Check before leasing
SELECT count(*) FROM jobs
WHERE tenant_id = :tenant_id
AND status IN ('leased', 'running');

-- If >= max_concurrent, skip this tenant's jobs
```

- Per-tenant concurrent job limits
- Default: 10 concurrent jobs per tenant
- Prevents one tenant from monopolizing workers

### Priority-Based Scheduling

Jobs are processed in priority order:

| Priority | Weight | Description |
|----------|--------|-------------|
| `critical` | 100 | Processed first |
| `high` | 10 | High priority |
| `normal` | 5 | Default priority |
| `low` | 1 | Background tasks |

---

## Observability

### Metrics (Prometheus)

| Metric | Type | Description |
|--------|------|-------------|
| `job_queue_depth{tenant_id}` | Gauge | Jobs waiting in queue |
| `jobs_submitted_total{tenant_id, priority}` | Counter | Total jobs submitted |
| `jobs_completed_total{tenant_id, status}` | Counter | Jobs completed by status |
| `job_duration_seconds{tenant_id, status}` | Histogram | Job execution duration |
| `lease_expired_total{tenant_id}` | Counter | Expired leases (crash recovery) |
| `lease_acquired_total{worker_id}` | Counter | Leases acquired by worker |
| `api_requests_total{method, endpoint, status}` | Counter | API request count |
| `api_request_latency_seconds{method, endpoint}` | Histogram | API latency |

### Tracing (OpenTelemetry)

One trace per job lifecycle with spans:

- `submit_job` - Job submission
- `acquire_lease` - Lease acquisition
- `execute_job` - Handler execution
- `ack_job` - Acknowledgment

Tags: `job_id`, `tenant_id`, `worker_id`, `attempt`

### Structured Logging (structlog)

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "info",
  "event": "Job completed successfully",
  "job_id": "abc123",
  "tenant_id": "acme",
  "duration": "1.23s",
  "attempt": 1
}
```

---

## Scaling Approach

### Horizontal Scaling

| Component | Scaling Strategy |
|-----------|------------------|
| **API Server** | Stateless, scale with HPA on CPU |
| **Workers** | Stateless, scale with queue depth |
| **Reaper** | Single instance (leader election for HA) |
| **PostgreSQL** | Vertical scaling, read replicas for queries |

### Kubernetes HPA Configuration

```yaml
# Workers scale based on CPU and custom metrics
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
spec:
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### Scaling Triggers

- **Queue depth > threshold**: Add workers
- **Oldest job age > threshold**: Add workers
- **CPU utilization > 70%**: Add API pods
- **Scale down stabilization**: 5 minutes (prevent flapping)

---

## Testing Strategy

### Unit Tests

```bash
poetry run pytest tests/unit/ -v
```

Tests include:
- Idempotency enforcement
- Lease expiry logic
- Retry → DLQ transitions
- Quota enforcement
- Rate limiting
- Job handlers

### Integration Tests

```bash
# Requires PostgreSQL (uses testcontainers)
poetry run pytest tests/integration/ -v
```

Tests include:
- Full job lifecycle
- Worker crash → lease expiry → re-execution
- Concurrent workers (no double execution)
- Tenant concurrency limits
- API endpoint testing

### Load Tests

```bash
# Start the system
docker compose up -d

# Run Locust
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
    --headless -u 100 -r 10 --run-time 5m
```

Validates:
- Burst submissions
- Multiple concurrent workers
- No duplicate executions
- Stable throughput
- Idempotency under load

### Kubernetes Smoke Test

```bash
# Deploy to K8s
kubectl apply -k k8s/

# Scale workers
kubectl scale deployment jobqueue-worker --replicas=5 -n jobqueue

# Submit jobs and observe
# - Queue drain speed
# - No duplicate execution
# - Proper lease handling
```

---

## Quick Start

### Prerequisites

- Python 3.13+
- Docker & Docker Compose
- PostgreSQL 16+ (or use Docker)

### Local Development

```bash
# Clone and setup
cd job-scheduler
poetry install

# Start PostgreSQL
docker compose up -d postgres

# Run migrations
poetry run alembic upgrade head

# Start API (terminal 1)
poetry run python -m uvicorn src.api.main:app --reload

# Start Worker (terminal 2)
poetry run python -m src.worker.main

# Start Reaper (terminal 3)
poetry run python -m src.reaper.main
```

### Docker Compose (Full Stack)

```bash
# Start everything
docker compose up -d

# Run migrations
docker compose --profile migrate up migrate

# View logs
docker compose logs -f

# With observability stack
docker compose --profile observability up -d

# With dashboard
docker compose --profile dashboard up -d
```

### Kubernetes

```bash
# Apply all manifests
kubectl apply -k k8s/

# Run migrations
kubectl apply -f k8s/migration-job.yaml

# Check status
kubectl get pods -n jobqueue
```

---

## API Reference

### Authentication

```bash
# Get token
curl -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "your-key", "tenant_id": "your-tenant"}'
```

### Submit Job

```bash
curl -X POST http://localhost:8000/v1/jobs \
  -H "Authorization: Bearer <token>" \
  -H "Idempotency-Key: unique-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "payload": {"job_type": "echo", "data": {"message": "Hello"}},
    "max_attempts": 3,
    "priority": "normal"
  }'
```

### Get Job Status

```bash
curl http://localhost:8000/v1/jobs/{job_id} \
  -H "Authorization: Bearer <token>"
```

### List Jobs

```bash
curl "http://localhost:8000/v1/jobs?status=queued&page=1&page_size=20" \
  -H "Authorization: Bearer <token>"
```

### Retry from DLQ

```bash
curl -X POST http://localhost:8000/v1/jobs/{job_id}/retry \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"reset_attempts": true}'
```

### WebSocket (Live Updates)

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/jobs?tenant_id=your-tenant');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Job update:', data);
};
```

---

## Trade-offs & Future Improvements

### Current Trade-offs

| Decision | Trade-off | Rationale |
|----------|-----------|-----------|
| PostgreSQL-only | No Kafka/Redis | Simplicity, durability, single source of truth |
| In-memory rate limiting | Not distributed | Simple, works for single API instance |
| Single reaper | No HA for reaper | Simplicity, short outage acceptable |
| Polling workers | Not push-based | Simple, avoids connection management |

### Future Improvements

1. **Distributed Rate Limiting**
   - Redis-based token buckets
   - Consistent across API instances

2. **Reaper High Availability**
   - Leader election with PostgreSQL advisory locks
   - Or use Kubernetes lease API

3. **Job Scheduling**
   - Cron-like recurring jobs
   - Delayed execution with precision

4. **Job Dependencies**
   - DAG-based job workflows
   - Parent-child job relationships

5. **Multi-Region**
   - PostgreSQL logical replication
   - Region-aware job routing

6. **Priority Queues**
   - Separate queues per priority
   - Dedicated workers for critical jobs

7. **Backpressure**
   - Dynamic rate limiting based on queue depth
   - Circuit breaker for downstream services

---

## Project Structure

```
job-scheduler/
├── src/
│   ├── api/              # FastAPI application
│   │   ├── routes/       # API endpoints
│   │   ├── auth.py       # JWT authentication
│   │   ├── rate_limit.py # Rate limiting
│   │   └── websocket.py  # WebSocket handler
│   ├── db/               # Database layer
│   │   ├── models.py     # SQLAlchemy models
│   │   └── repository.py # Data access
│   ├── worker/           # Job workers
│   │   ├── handlers.py   # Job handlers
│   │   └── main.py       # Worker process
│   ├── reaper/           # Lease reaper
│   ├── observability/    # Metrics, tracing, logging
│   ├── types/            # Type definitions
│   ├── config.py         # Configuration
│   └── constants.py      # Constants
├── tests/
│   ├── unit/             # Unit tests
│   ├── integration/      # Integration tests
│   └── load/             # Load tests (Locust)
├── dashboard/            # Next.js dashboard
├── k8s/                  # Kubernetes manifests
├── alembic/              # Database migrations
├── docker-compose.yml    # Local development
├── Dockerfile            # API container
└── pyproject.toml        # Python dependencies
```

---

## License

MIT License - See LICENSE file for details.

---

**Simple, durable, correct systems beat clever ones.**
