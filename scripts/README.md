# Scripts Directory

This directory contains utility scripts for deploying, testing, and managing the job scheduler.

## 📋 Script Overview

| Script | Purpose | Usage |
|--------|---------|-------|
| **deploy-local-k8s.sh** | Deploy to local Kubernetes | `./deploy-local-k8s.sh [--clean\|--skip-build]` |
| **create-demo-jobs.sh** | Create demo jobs for testing | `./create-demo-jobs.sh [tenant-id] [count]` |
| **test-rate-limit.sh** | Test rate limiting | `./test-rate-limit.sh [tenant-id] [requests]` |
| **run-locust-test.sh** | Run Locust load tests | `./run-locust-test.sh [mode] [users] [spawn-rate] [duration]` |
| **k8s-load-test.sh** | Simple bash load test | `./k8s-load-test.sh [jobs] [concurrent]` |
| **trace-request.sh** | Find logs by trace ID | `./trace-request.sh [trace-id]` |
| **check-traces.sh** | Show Jaeger info | `./check-traces.sh` |
| **run.sh** | Run API locally | `./run.sh` |
| **run_tests.sh** | Run test suite | `./run_tests.sh` |

## 🚀 Deployment

### Deploy to Local Kubernetes

```bash
# Full deployment
./deploy-local-k8s.sh

# Clean deployment (remove existing first)
./deploy-local-k8s.sh --clean

# Skip image building (use existing images)
./deploy-local-k8s.sh --skip-build
```

**What it does:**
- Detects/starts Minikube or Kind
- Builds Docker images
- Creates namespace and secrets
- Deploys PostgreSQL, API, Workers, Reaper, Dashboard
- Runs database migrations
- Shows access information

## 🧪 Testing

### Create Demo Jobs

```bash
# Create 10 jobs for demo-tenant
./create-demo-jobs.sh demo-tenant 10

# Create 1 job (default tenant)
./create-demo-jobs.sh
```

### Rate Limiting Test

```bash
# Quick test (150 requests)
./test-rate-limit.sh

# Test specific tenant with 300 requests
./test-rate-limit.sh my-tenant 300

# Test multiple tenants
./test-rate-limit.sh tenant-1 200
./test-rate-limit.sh tenant-2 200
```

**Expected:**
- First ~200 requests succeed (burst capacity)
- Subsequent requests get 429 (rate limited)
- Shows success/rate-limited/error counts

### Load Testing

**Simple bash-based:**
```bash
# 1000 jobs, 50 concurrent
./k8s-load-test.sh 1000 50
```

**Locust (comprehensive):**
```bash
# Web UI (interactive)
./run-locust-test.sh web

# Headless with reports
./run-locust-test.sh headless 200 20 10m

# Specific scenarios
./run-locust-test.sh normal 100 10 5m      # Normal traffic
./run-locust-test.sh burst 100 10 5m       # Burst traffic  
./run-locust-test.sh idempotency 50 10 3m  # Idempotency test
```

## 🔍 Tracing & Debugging

### Trace a Request

```bash
# Automatically find recent trace
./trace-request.sh

# Search for specific trace ID
./trace-request.sh a1b2c3d4e5f6789012345678901234567890
```

### Check Jaeger Setup

```bash
./check-traces.sh
```

Shows Jaeger UI access info and instructions.

## 🏃 Local Development

### Run API Locally

```bash
./run.sh
```

Starts the API server on http://localhost:8000

### Run Tests

```bash
./run_tests.sh
```

Runs the full test suite (unit, integration, load tests).

## 📝 Script Details

### deploy-local-k8s.sh

**Options:**
- `--clean` - Delete existing deployment first
- `--skip-build` - Skip Docker image building
- `--help` - Show help message

**Environment Detection:**
- Auto-detects Minikube or Kind
- Falls back to starting Minikube if neither running

**Outputs:**
- API URL and access instructions
- Dashboard URL
- Deployment status

### run-locust-test.sh

**Modes:**
- `web` - Start Web UI (default)
- `headless` - Run headless with CSV/HTML reports
- `normal` - Test normal traffic pattern
- `burst` - Test burst traffic pattern
- `idempotency` - Test idempotency guarantees

**Parameters:**
- `users` - Number of concurrent users (default: 100)
- `spawn-rate` - Users spawned per second (default: 10)
- `duration` - Test duration, e.g., "5m", "1h" (default: 5m)

**Examples:**
```bash
./run-locust-test.sh web
./run-locust-test.sh headless 200 20 10m
./run-locust-test.sh burst 100 10 5m
```

### test-rate-limit.sh

**Parameters:**
- `tenant-id` - Tenant to test (default: rate-test-tenant)
- `requests` - Number of requests to send (default: 150)

**Output:**
- Success count
- Rate limited count
- Error count
- Rate: requests/second

### create-demo-jobs.sh

**Parameters:**
- `tenant-id` - Tenant ID (default: demo-tenant)
- `count` - Number of jobs to create (default: 10)

**Features:**
- Auto port-forwards if needed
- Gets auth token automatically
- Shows creation progress
- Provides verification commands

### trace-request.sh

**Usage:**
```bash
# Auto-find recent trace
./trace-request.sh

# Search specific trace
./trace-request.sh <trace-id>
```

**Output:**
- API logs with trace ID
- Worker logs with trace ID
- Reaper logs with trace ID
- Jaeger UI instructions

## 🛠️ Requirements

All scripts require:
- `kubectl` configured for your cluster
- For local deployment: Minikube or Kind
- For Locust tests: `pip install locust`

## 💡 Tips

1. **Port-forwarding**: Most scripts auto-start port-forwarding if needed
2. **Cleanup**: Scripts clean up port-forwards on exit
3. **Errors**: Check script output for detailed error messages
4. **Logs**: Use `kubectl logs` for detailed debugging

## 📚 See Also

- Main README: `../README.md`
- Kubernetes manifests: `../k8s/`
- Locust tests: `../tests/load/locustfile.py`
