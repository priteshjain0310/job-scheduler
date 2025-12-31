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

## Kubernetes Deployment

### 🚀 Quick Deploy to Local Kubernetes

**One-command deployment:**

```bash
./scripts/deploy-local-k8s.sh
```

This script will:
- ✅ Detect/start Minikube or Kind
- ✅ Build all Docker images locally
- ✅ Create namespace and secrets
- ✅ Deploy PostgreSQL, API, Workers, Reaper, and Dashboard
- ✅ Run database migrations
- ✅ Show access information

**Options:**
```bash
./scripts/deploy-local-k8s.sh --clean        # Remove existing deployment first
./scripts/deploy-local-k8s.sh --skip-build   # Skip image building
./scripts/deploy-local-k8s.sh --help         # Show help
```

### 📋 Prerequisites

- **Docker** - [Install Docker](https://docs.docker.com/get-docker/)
- **kubectl** - [Install kubectl](https://kubernetes.io/docs/tasks/tools/)
- **Minikube** (recommended) or **Kind** for local clusters

### 🔍 What Gets Deployed

| Component | Replicas | Auto-Scale | Purpose |
|-----------|----------|------------|---------|
| **PostgreSQL** | 1 | No | Database (StatefulSet) |
| **API** | 3 | 2-10 | REST API endpoints |
| **Worker** | 3 | 2-20 | Job processors |
| **Reaper** | 1 | No | Lease recovery |
| **Dashboard** | 2 | 2-10 | Web UI |

### 🌐 Access Services

**API:**
```bash
kubectl port-forward svc/jobqueue-api-service 8000:80 -n jobqueue
# Access: http://localhost:8000
# Swagger: http://localhost:8000/docs
# Metrics: http://localhost:8000/metrics
```

**Dashboard:**
```bash
kubectl port-forward svc/jobqueue-dashboard-service 3000:80 -n jobqueue
# Access: http://localhost:3000
```

**For Minikube:**
```bash
minikube service jobqueue-api-service -n jobqueue  # Opens in browser
```

### 🧪 Test the Deployment

```bash
# Create a test job
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "demo-tenant",
    "idempotency_key": "test-1",
    "payload": {
      "job_type": "echo",
      "data": {"message": "Hello Kubernetes!"}
    }
  }'

# List jobs
curl "http://localhost:8000/api/v1/jobs?tenant_id=demo-tenant"
```

### 📊 Monitor Your Deployment

```bash
# View all pods
kubectl get pods -n jobqueue

# View logs
kubectl logs -f deployment/jobqueue-api -n jobqueue
kubectl logs -f deployment/jobqueue-worker -n jobqueue
kubectl logs -f deployment/jobqueue-dashboard -n jobqueue

# Watch auto-scaling
kubectl get hpa -n jobqueue --watch

# Check resource usage
kubectl top pods -n jobqueue
```

### ⚙️ Scale Workers

```bash
# Manual scaling
kubectl scale deployment jobqueue-worker --replicas=10 -n jobqueue

# Auto-scaling is already configured (2-20 replicas based on CPU)
```

### 🧹 Cleanup

```bash
kubectl delete namespace jobqueue
```

### 🏗️ Production Deployment (EKS/GKE/AKS)

**1. Build and Push Images:**

```bash
# Set your registry
export REGISTRY=ghcr.io/your-org
export VERSION=v1.0.0

# Build and push
docker build -f Dockerfile.api -t ${REGISTRY}/jobqueue-api:${VERSION} .
docker build -f Dockerfile.worker -t ${REGISTRY}/jobqueue-worker:${VERSION} .
docker build -f Dockerfile.reaper -t ${REGISTRY}/jobqueue-reaper:${VERSION} .
docker build -f dashboard/Dockerfile -t ${REGISTRY}/jobqueue-dashboard:${VERSION} ./dashboard

docker push ${REGISTRY}/jobqueue-api:${VERSION}
docker push ${REGISTRY}/jobqueue-worker:${VERSION}
docker push ${REGISTRY}/jobqueue-reaper:${VERSION}
docker push ${REGISTRY}/jobqueue-dashboard:${VERSION}
```

**2. Update Image References:**

```bash
# Update k8s manifests with your registry and version
sed -i "s|image: jobqueue-api:latest|image: ${REGISTRY}/jobqueue-api:${VERSION}|g" k8s/api.yaml
sed -i "s|image: jobqueue-worker:latest|image: ${REGISTRY}/jobqueue-worker:${VERSION}|g" k8s/worker.yaml
sed -i "s|image: jobqueue-reaper:latest|image: ${REGISTRY}/jobqueue-reaper:${VERSION}|g" k8s/reaper.yaml
sed -i "s|image: jobqueue-dashboard:latest|image: ${REGISTRY}/jobqueue-dashboard:${VERSION}|g" k8s/dashboard.yaml
sed -i "s|imagePullPolicy: Never|imagePullPolicy: IfNotPresent|g" k8s/*.yaml
```

**3. Create Secrets:**

```bash
# Generate secure passwords
DB_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)
API_SECRET=$(openssl rand -base64 32)

# Create secret
kubectl create secret generic jobqueue-secret \
  --from-literal=DATABASE_URL="postgresql+asyncpg://postgres:${DB_PASSWORD}@postgres-service:5432/jobqueue" \
  --from-literal=API_SECRET_KEY="${API_SECRET}" \
  --from-literal=POSTGRES_USER="postgres" \
  --from-literal=POSTGRES_PASSWORD="${DB_PASSWORD}" \
  --namespace=jobqueue
```

**4. Deploy:**

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/postgres.yaml
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=postgres -n jobqueue --timeout=300s
kubectl apply -f k8s/migration-job.yaml
kubectl wait --for=condition=complete job/jobqueue-migrate -n jobqueue --timeout=300s
kubectl apply -f k8s/api.yaml
kubectl apply -f k8s/worker.yaml
kubectl apply -f k8s/reaper.yaml
kubectl apply -f k8s/dashboard.yaml
```

**5. Optional Production Features:**

```bash
# Pod Disruption Budgets
kubectl apply -f k8s/pdb.yaml

# Network Policies
kubectl apply -f k8s/network-policy.yaml

# Prometheus Monitoring (requires Prometheus Operator)
kubectl apply -f k8s/servicemonitor.yaml

# Ingress (update with your domain)
kubectl apply -f k8s/ingress.yaml
```

### 🔄 CI/CD Pipeline

GitHub Actions workflow included (`.github/workflows/deploy.yaml`):

**On every push:**
1. ✅ Run tests with PostgreSQL
2. ✅ Build Docker images for API, Worker, Reaper, Dashboard
3. ✅ Push to container registry
4. ✅ Deploy to staging (develop branch)
5. ✅ Deploy to production (main branch)

**Setup:**
1. Add secrets to GitHub repository:
   - `KUBE_CONFIG_STAGING` - Kubeconfig for staging cluster
   - `KUBE_CONFIG_PROD` - Kubeconfig for production cluster
   - `SLACK_WEBHOOK` - (Optional) For deployment notifications

2. Update registry in `.github/workflows/deploy.yaml`:
   ```yaml
   env:
     REGISTRY: ghcr.io
     IMAGE_PREFIX: ${{ github.repository }}
   ```

### 📈 Monitoring & Observability

**Key Metrics:**

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `job_queue_depth` | Jobs waiting to be processed | > 1000 |
| `worker_count` | Active worker pods | < 2 |
| `job_processing_time_p95` | 95th percentile processing time | > 300s |
| `job_failed_total` | Total failed jobs | Rate > 10% |
| `api_request_duration_p95` | API latency (95th percentile) | > 1s |

**Access Metrics:**
```bash
# Prometheus format metrics
curl http://localhost:8000/metrics
```

### 🐛 Troubleshooting

**Workers Not Scaling:**
```bash
# Check HPA status
kubectl describe hpa jobqueue-worker-hpa -n jobqueue

# Check metrics server
kubectl top nodes
kubectl top pods -n jobqueue
```

**Jobs Not Processing:**
```bash
# Check worker logs
kubectl logs -f deployment/jobqueue-worker -n jobqueue

# Check job count in database
kubectl exec -it postgres-0 -n jobqueue -- \
  psql -U postgres jobqueue -c "SELECT status, COUNT(*) FROM jobs GROUP BY status;"
```

**Database Connection Issues:**
```bash
# Test connectivity
kubectl run -it --rm debug --image=postgres:16-alpine -n jobqueue -- \
  psql postgresql://postgres:password@postgres-service:5432/jobqueue

# Check PostgreSQL logs
kubectl logs -f postgres-0 -n jobqueue
```

### 🎯 Scaling Recommendations

**Small Workload (< 1,000 jobs/hour):**
- API: 2 replicas
- Workers: 2-5 replicas
- Database: Single instance

**Medium Workload (1,000-10,000 jobs/hour):**
- API: 3-5 replicas
- Workers: 5-15 replicas (HPA enabled)
- Database: Consider read replicas

**Large Workload (> 10,000 jobs/hour):**
- API: 5-10 replicas
- Workers: 10-50 replicas (HPA enabled)
- Database: Managed service (RDS, Cloud SQL, Azure Database)
- Consider: PgBouncer for connection pooling

### 🔐 Security Checklist

- [x] Non-root containers (UID 1000)
- [x] Network policies configured
- [x] Secrets for sensitive data
- [x] Pod disruption budgets
- [x] Resource limits set
- [ ] TLS/SSL on ingress (configure cert-manager)
- [ ] RBAC policies (use service accounts)
- [ ] Image scanning (add to CI/CD)
- [ ] Audit logging enabled

---

## Load Testing

### Quick Load Test

```bash
# Simple bash-based test
./scripts/k8s-load-test.sh 1000 50  # 1000 jobs, 50 concurrent
```

### Locust Load Testing (Recommended)

**Install Locust:**
```bash
pip install locust
```

**Run tests:**

```bash
# Interactive Web UI
./scripts/run-locust-test.sh web
# Open http://localhost:8089

# Headless with reports
./scripts/run-locust-test.sh headless 200 20 10m

# Specific scenarios
./scripts/run-locust-test.sh normal 100 10 5m      # Normal traffic
./scripts/run-locust-test.sh burst 100 10 5m       # Burst traffic
./scripts/run-locust-test.sh idempotency 50 10 3m  # Idempotency test
```

**Test Scenarios:**
- **JobSchedulerUser** - Normal traffic (job creation, status checks, listing)
- **BurstSubmissionUser** - Burst traffic (10-50 jobs in rapid bursts)
- **IdempotencyTestUser** - Validates no duplicate jobs created

**Performance Benchmarks:**

| Metric | Good | Acceptable | Poor |
|--------|------|------------|------|
| Response Time (p50) | < 100ms | 100-300ms | > 300ms |
| Response Time (p95) | < 500ms | 500-1000ms | > 1000ms |
| Throughput | > 100 req/s | 50-100 req/s | < 50 req/s |
| Failure Rate | < 0.1% | 0.1-1% | > 1% |

**Monitor during tests:**
```bash
# Watch worker auto-scaling
kubectl get hpa -n jobqueue --watch

# Monitor resources
watch kubectl top pods -n jobqueue

# Check job processing
kubectl exec -it postgres-0 -n jobqueue -- \
  psql -U postgres jobqueue -c \
  "SELECT status, COUNT(*) FROM jobs GROUP BY status;"
```

---

## Rate Limiting

### Overview

Per-tenant rate limiting using token bucket algorithm:
- **Default limit**: 100 requests/minute per tenant
- **Burst capacity**: 200 requests (2x rate)
- **Per-tenant isolation**: Independent limits for each tenant

### Test Rate Limiting

```bash
# Quick test (150 requests to trigger rate limiting)
./scripts/test-rate-limit.sh

# Test specific tenant
./scripts/test-rate-limit.sh my-tenant 300

# Test multiple tenants (independent limits)
./scripts/test-rate-limit.sh tenant-1 200
./scripts/test-rate-limit.sh tenant-2 200
```

**Expected results:**
- First ~200 requests succeed (burst capacity)
- Subsequent requests get 429 Too Many Requests
- Tokens refill at ~1.67/second

### Configure Rate Limits

**Update ConfigMap:**
```bash
kubectl edit configmap jobqueue-config -n jobqueue

# Add or modify:
data:
  RATE_LIMIT_REQUESTS_PER_MINUTE: "50"  # Change from default 100

# Restart API
kubectl rollout restart deployment/jobqueue-api -n jobqueue
```

**Rate limit response:**
```json
HTTP 429 Too Many Requests
{
  "error": "Rate limit exceeded. Retry after 5.2 seconds"
}
Headers: Retry-After: 6
```

### Client-Side Handling

```python
import time
import requests

def create_job_with_retry(data, max_retries=3):
    for attempt in range(max_retries):
        response = requests.post(url, json=data)
        
        if response.status_code == 201:
            return response.json()
        
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 1))
            time.sleep(retry_after)
            continue
        
        response.raise_for_status()
```

---

## Request Tracing

### Overview

Distributed tracing with OpenTelemetry:
- Trace IDs in all logs
- Jaeger for visual trace exploration
- End-to-end request tracking (API → Worker → Completion)

### Deploy Jaeger

```bash
# Deploy Jaeger to Kubernetes
kubectl apply -f k8s/jaeger.yaml

# Restart pods to enable trace logging
kubectl rollout restart deployment/jobqueue-api -n jobqueue
kubectl rollout restart deployment/jobqueue-worker -n jobqueue

# Access Jaeger UI
kubectl port-forward svc/jaeger-query -n jobqueue 16686:16686
open http://localhost:16686
```

### Trace a Request

**Method 1: Using Jaeger UI (Recommended)**

1. Open Jaeger UI at http://localhost:16686
2. Select service: `jobqueue-api`
3. Click "Find Traces"
4. Click any trace to see full timeline

**Method 2: Using Logs**

```bash
# Create a job
./scripts/create-demo-jobs.sh demo-tenant 1

# Find trace ID in logs
TRACE_ID=$(kubectl logs -l app.kubernetes.io/name=jobqueue-api -n jobqueue --tail=100 | \
  grep "Job created" | tail -1 | grep -o '"trace_id":"[^"]*"' | cut -d'"' -f4)

# Search all logs for this trace
./scripts/trace-request.sh $TRACE_ID
```

**What you see in traces:**
- Full request timeline
- All spans with durations (API → DB → Worker)
- Performance bottlenecks
- Error traces highlighted
- Service dependencies

### Trace Context in Logs

After rebuilding images, all logs include:
```json
{
  "event": "Job created",
  "trace_id": "a1b2c3d4e5f6789012345678901234567890",
  "span_id": "1234567890abcdef",
  "job_id": "uuid-here",
  "tenant_id": "demo-tenant",
  "timestamp": "2025-12-31T07:30:00.000Z"
}
```

---

## License

MIT License - See LICENSE file for details.

---

**Simple, durable, correct systems beat clever ones.**
