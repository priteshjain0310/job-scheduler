# Kubernetes Deployment Guide

## Overview

This guide covers deploying the Job Scheduler to Kubernetes with production-ready configurations including:
- Horizontal Pod Autoscaling (HPA)
- Graceful shutdown handling
- Health checks and readiness probes
- Resource limits and requests
- Database migrations
- Monitoring and observability

## Architecture

```
┌─────────────────┐
│   Ingress/LB    │
└────────┬────────┘
         │
    ┌────▼─────┐
    │   API    │ (3+ replicas, HPA)
    │ Service  │
    └────┬─────┘
         │
    ┌────▼─────────────────┐
    │    PostgreSQL        │
    │   (StatefulSet)      │
    └────┬─────────────────┘
         │
    ┌────▼─────┐    ┌──────────┐
    │  Worker  │    │  Reaper  │
    │ (2-20)   │    │  (1)     │
    └──────────┘    └──────────┘
```

## Prerequisites

1. **Kubernetes Cluster** (v1.24+)
   - Minikube, Kind, GKE, EKS, or AKS

2. **kubectl** configured to access your cluster

3. **Docker** for building images

4. **Metrics Server** (for HPA)
   ```bash
   kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
   ```

## Quick Start

### 1. Build Docker Images

```bash
# Build all images
docker build -f Dockerfile.api -t jobqueue-api:latest .
docker build -f Dockerfile.worker -t jobqueue-worker:latest .
docker build -f Dockerfile.reaper -t jobqueue-reaper:latest .

# Tag for your registry (replace with your registry)
docker tag jobqueue-api:latest your-registry/jobqueue-api:v1.0.0
docker tag jobqueue-worker:latest your-registry/jobqueue-worker:v1.0.0
docker tag jobqueue-reaper:latest your-registry/jobqueue-reaper:v1.0.0

# Push to registry
docker push your-registry/jobqueue-api:v1.0.0
docker push your-registry/jobqueue-worker:v1.0.0
docker push your-registry/jobqueue-reaper:v1.0.0
```

### 2. Configure Secrets

Create a `k8s/secret-values.yaml` (DO NOT commit this file):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: jobqueue-secret
  namespace: jobqueue
type: Opaque
stringData:
  DATABASE_URL: "postgresql+asyncpg://postgres:YOUR_PASSWORD@postgres:5432/jobqueue"
  API_SECRET_KEY: "your-secret-key-min-32-chars-long"
  POSTGRES_PASSWORD: "YOUR_PASSWORD"
```

Apply the secret:
```bash
kubectl apply -f k8s/secret-values.yaml
```

### 3. Deploy to Kubernetes

```bash
# Create namespace
kubectl apply -f k8s/namespace.yaml

# Apply all manifests
kubectl apply -f k8s/

# Or use kustomize
kubectl apply -k k8s/
```

### 4. Run Database Migrations

```bash
# Wait for PostgreSQL to be ready
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=postgres -n jobqueue --timeout=300s

# Run migrations
kubectl apply -f k8s/migration-job.yaml

# Check migration status
kubectl logs -f job/jobqueue-migration -n jobqueue
```

### 5. Verify Deployment

```bash
# Check all pods are running
kubectl get pods -n jobqueue

# Check services
kubectl get svc -n jobqueue

# Check HPA status
kubectl get hpa -n jobqueue

# View API logs
kubectl logs -f deployment/jobqueue-api -n jobqueue

# View worker logs
kubectl logs -f deployment/jobqueue-worker -n jobqueue
```

## Scaling

### Manual Scaling

```bash
# Scale API
kubectl scale deployment jobqueue-api -n jobqueue --replicas=5

# Scale workers
kubectl scale deployment jobqueue-worker -n jobqueue --replicas=10
```

### Horizontal Pod Autoscaling (HPA)

Workers automatically scale based on CPU usage (70% threshold):
- **Min replicas**: 2
- **Max replicas**: 20
- **Scale up**: Fast (100% increase every 15s, max 4 pods)
- **Scale down**: Slow (10% decrease every 60s, 5min stabilization)

Monitor HPA:
```bash
kubectl get hpa jobqueue-worker-hpa -n jobqueue --watch
```

### Custom Metrics (Optional)

For queue-depth based scaling, install Prometheus Adapter:

```bash
# Install Prometheus Operator
helm install prometheus prometheus-community/kube-prometheus-stack -n monitoring

# Configure custom metric in worker.yaml (uncomment lines 60-66)
```

## Configuration

### Environment Variables

Configured in `k8s/configmap.yaml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FORMAT` | `json` | Log format (json/console) |
| `WORKER_BATCH_SIZE` | `10` | Jobs per worker poll |
| `WORKER_POLL_INTERVAL_SECONDS` | `1.0` | Poll interval |
| `WORKER_LEASE_DURATION_SECONDS` | `300` | Lease duration |
| `REAPER_INTERVAL_SECONDS` | `60` | Reaper check interval |

Update ConfigMap:
```bash
kubectl edit configmap jobqueue-config -n jobqueue
# Restart pods to pick up changes
kubectl rollout restart deployment/jobqueue-worker -n jobqueue
```

### Resource Limits

#### API Service
- **Requests**: 100m CPU, 256Mi memory
- **Limits**: 500m CPU, 512Mi memory

#### Worker Service
- **Requests**: 100m CPU, 256Mi memory
- **Limits**: 500m CPU, 512Mi memory

#### Reaper Service
- **Requests**: 50m CPU, 128Mi memory
- **Limits**: 200m CPU, 256Mi memory

Adjust in respective YAML files based on your workload.

## Health Checks

### API Service

**Liveness Probe**: `/health`
- Initial delay: 10s
- Period: 30s
- Timeout: 5s

**Readiness Probe**: `/ready`
- Initial delay: 5s
- Period: 10s
- Timeout: 5s

### Worker Service

Workers don't have HTTP endpoints. Health is determined by:
- Process running
- Graceful shutdown on SIGTERM (60s grace period)

## Graceful Shutdown

### Worker Behavior on Pod Termination

1. **SIGTERM received** (from `kubectl delete pod` or scaling down)
2. Worker stops accepting new jobs
3. Current jobs complete (up to 60s grace period)
4. Worker exits cleanly

```python
# Already implemented in src/worker/main.py
for sig in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.stop()))
```

### Grace Period Configuration

Set in `k8s/worker.yaml`:
```yaml
terminationGracePeriodSeconds: 60
```

Increase if jobs take longer than 60s to complete.

## Monitoring

### Metrics Endpoint

API exposes Prometheus metrics at `/metrics`:

```bash
# Port-forward to access metrics
kubectl port-forward svc/jobqueue-api 8000:80 -n jobqueue

# View metrics
curl http://localhost:8000/metrics
```

### Key Metrics

- `job_created_total` - Jobs created
- `job_completed_total` - Jobs completed successfully
- `job_failed_total` - Jobs failed
- `job_queue_depth` - Current queue size
- `worker_lease_acquired_total` - Leases acquired by workers

### Prometheus Integration

Add ServiceMonitor for Prometheus Operator:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: jobqueue-api
  namespace: jobqueue
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: jobqueue-api
  endpoints:
  - port: http
    path: /metrics
    interval: 30s
```

## Logging

### Structured Logging

All services log in JSON format (configurable via `LOG_FORMAT`):

```json
{
  "timestamp": "2025-12-31T10:00:00Z",
  "level": "INFO",
  "message": "Job completed successfully",
  "job_id": "123e4567-e89b-12d3-a456-426614174000",
  "worker_id": "worker-pod-abc123"
}
```

### View Logs

```bash
# API logs
kubectl logs -f deployment/jobqueue-api -n jobqueue

# Worker logs (all pods)
kubectl logs -f -l app.kubernetes.io/name=jobqueue-worker -n jobqueue

# Specific worker pod
kubectl logs -f jobqueue-worker-abc123 -n jobqueue

# Reaper logs
kubectl logs -f deployment/jobqueue-reaper -n jobqueue
```

### Log Aggregation

For production, use:
- **Fluentd/Fluent Bit** → Elasticsearch → Kibana
- **Promtail** → Loki → Grafana
- **CloudWatch** (AWS), **Stackdriver** (GCP), **Azure Monitor**

## Database Management

### Backup

```bash
# Create backup
kubectl exec -n jobqueue postgres-0 -- pg_dump -U postgres jobqueue > backup.sql

# Restore backup
kubectl exec -i -n jobqueue postgres-0 -- psql -U postgres jobqueue < backup.sql
```

### Production Database

For production, use managed database services:
- **AWS RDS** for PostgreSQL
- **Google Cloud SQL**
- **Azure Database for PostgreSQL**

Update `DATABASE_URL` in secret to point to managed database.

## Troubleshooting

### Pods Not Starting

```bash
# Check pod status
kubectl describe pod <pod-name> -n jobqueue

# Check events
kubectl get events -n jobqueue --sort-by='.lastTimestamp'

# Check image pull
kubectl get pods -n jobqueue -o jsonpath='{.items[*].status.containerStatuses[*].state}'
```

### Database Connection Issues

```bash
# Test database connectivity
kubectl run -it --rm debug --image=postgres:16-alpine --restart=Never -n jobqueue -- \
  psql postgresql://postgres:password@postgres:5432/jobqueue

# Check PostgreSQL logs
kubectl logs -f postgres-0 -n jobqueue
```

### Worker Not Processing Jobs

```bash
# Check worker logs
kubectl logs -f deployment/jobqueue-worker -n jobqueue

# Check if jobs exist
kubectl exec -it postgres-0 -n jobqueue -- \
  psql -U postgres jobqueue -c "SELECT COUNT(*) FROM jobs WHERE status='queued';"

# Check HPA status
kubectl describe hpa jobqueue-worker-hpa -n jobqueue
```

### High Memory Usage

```bash
# Check resource usage
kubectl top pods -n jobqueue

# Adjust resource limits in deployment YAML
# Restart deployment
kubectl rollout restart deployment/jobqueue-worker -n jobqueue
```

## Security Best Practices

1. **Use Secrets for sensitive data**
   - Never commit secrets to Git
   - Use external secret managers (AWS Secrets Manager, Vault)

2. **Network Policies**
   - Restrict pod-to-pod communication
   - Only allow necessary ingress/egress

3. **RBAC**
   - Use service accounts with minimal permissions
   - Don't use default service account

4. **Image Security**
   - Scan images for vulnerabilities
   - Use non-root users (already configured)
   - Keep base images updated

5. **TLS/SSL**
   - Use cert-manager for automatic certificate management
   - Configure ingress with TLS

## Production Checklist

- [ ] Use managed PostgreSQL (RDS, Cloud SQL, etc.)
- [ ] Configure persistent volumes with backups
- [ ] Set up monitoring and alerting
- [ ] Configure log aggregation
- [ ] Enable HPA with appropriate metrics
- [ ] Set resource requests/limits based on load testing
- [ ] Configure ingress with TLS
- [ ] Set up CI/CD pipeline
- [ ] Configure network policies
- [ ] Enable pod disruption budgets
- [ ] Set up disaster recovery plan
- [ ] Document runbooks for common issues

## CI/CD Integration

See `k8s/ci-cd-example.yaml` for GitHub Actions example.

## Support

For issues or questions:
1. Check logs: `kubectl logs -f <pod-name> -n jobqueue`
2. Check events: `kubectl get events -n jobqueue`
3. Review metrics: Access `/metrics` endpoint
4. Consult troubleshooting section above
