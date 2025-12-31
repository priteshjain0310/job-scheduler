#!/bin/bash
set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}🚀 Kubernetes Load Test${NC}"
echo ""

# Check if API is accessible
echo "Checking API accessibility..."
kubectl port-forward svc/jobqueue-api-service 8000:80 -n jobqueue &
PF_PID=$!
sleep 3

# Wait for port-forward to be ready
until curl -s http://localhost:8000/health > /dev/null 2>&1; do
    echo "Waiting for API to be accessible..."
    sleep 2
done

echo -e "${GREEN}✓ API is accessible${NC}"
echo ""

# Load test parameters
TOTAL_JOBS=${1:-1000}
CONCURRENT=${2:-50}
TENANT_ID="load-test-$(date +%s)"

echo -e "${YELLOW}Load Test Configuration:${NC}"
echo "  Total Jobs: $TOTAL_JOBS"
echo "  Concurrent: $CONCURRENT"
echo "  Tenant ID: $TENANT_ID"
echo ""

# Get auth token
echo "Getting auth token..."
TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token" \
  -H "Content-Type: application/json" \
  -d "{\"tenant_id\": \"${TENANT_ID}\", \"api_key\": \"load-test-key\"}" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

if [ -z "$TOKEN" ]; then
    echo "Failed to get auth token. Check API logs."
    kill $PF_PID 2>/dev/null || true
    exit 1
fi

echo -e "${GREEN}✓ Got auth token${NC}"
echo ""

# Create jobs
echo "Creating $TOTAL_JOBS jobs with $CONCURRENT concurrent requests..."
START_TIME=$(date +%s)

for i in $(seq 1 $TOTAL_JOBS); do
    curl -X POST "http://localhost:8000/v1/jobs" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Idempotency-Key: load-test-${i}" \
      -d "{
        \"tenant_id\": \"${TENANT_ID}\",
        \"payload\": {
          \"job_type\": \"echo\",
          \"data\": {\"message\": \"Load test job ${i}\"}
        }
      }" \
      -s -o /dev/null &
    
    # Limit concurrent requests
    if [ $((i % CONCURRENT)) -eq 0 ]; then
        wait
    fi
done

wait
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
echo -e "${GREEN}✓ Created $TOTAL_JOBS jobs in ${DURATION}s${NC}"
echo "  Rate: $((TOTAL_JOBS / DURATION)) jobs/sec"
echo ""

# Monitor processing
echo "Monitoring job processing..."
echo "Press Ctrl+C to stop monitoring"
echo ""

while true; do
    STATS=$(kubectl exec -it postgres-0 -n jobqueue -- \
        psql -U postgres jobqueue -t -c \
        "SELECT status, COUNT(*) FROM jobs WHERE tenant_id='${TENANT_ID}' GROUP BY status;" 2>/dev/null | grep -v "^$" || echo "")
    
    clear
    echo -e "${GREEN}Job Processing Status${NC}"
    echo "====================="
    echo "$STATS"
    echo ""
    echo "Worker Pods:"
    kubectl get pods -l app.kubernetes.io/name=jobqueue-worker -n jobqueue --no-headers | wc -l
    echo ""
    echo "HPA Status:"
    kubectl get hpa jobqueue-worker-hpa -n jobqueue --no-headers 2>/dev/null || echo "N/A"
    
    sleep 2
done

# Cleanup
kill $PF_PID 2>/dev/null || true
