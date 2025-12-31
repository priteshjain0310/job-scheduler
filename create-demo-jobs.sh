#!/bin/bash
set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}🎯 Creating Demo Jobs${NC}"
echo ""

# Configuration
TENANT_ID=${1:-"demo-tenant"}
NUM_JOBS=${2:-10}
API_URL="http://localhost:8000"

echo -e "${YELLOW}Configuration:${NC}"
echo "  Tenant ID: $TENANT_ID"
echo "  Number of Jobs: $NUM_JOBS"
echo ""

# Check if port-forward is needed
if ! curl -s ${API_URL}/health > /dev/null 2>&1; then
    echo "Starting port-forward..."
    kubectl port-forward svc/jobqueue-api-service 8000:80 -n jobqueue &
    PF_PID=$!
    sleep 3
    
    until curl -s ${API_URL}/health > /dev/null 2>&1; do
        echo "Waiting for API..."
        sleep 2
    done
fi

echo -e "${GREEN}✓ API is accessible${NC}"
echo ""

# Get auth token
echo "Getting auth token..."
TOKEN=$(curl -s -X POST "${API_URL}/auth/token" \
  -H "Content-Type: application/json" \
  -d "{\"tenant_id\": \"${TENANT_ID}\", \"api_key\": \"demo-api-key\"}" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

if [ -z "$TOKEN" ]; then
    echo "Failed to get auth token"
    [ ! -z "$PF_PID" ] && kill $PF_PID 2>/dev/null || true
    exit 1
fi

echo -e "${GREEN}✓ Got auth token${NC}"
echo ""

# Create jobs
echo "Creating $NUM_JOBS jobs..."
for i in $(seq 1 $NUM_JOBS); do
    RESPONSE=$(curl -s -X POST "${API_URL}/v1/jobs" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Idempotency-Key: demo-job-$(date +%s)-${i}" \
      -d "{
        \"tenant_id\": \"${TENANT_ID}\",
        \"payload\": {
          \"job_type\": \"echo\",
          \"data\": {\"message\": \"Demo job ${i}\", \"index\": ${i}}
        },
        \"priority\": \"normal\"
      }")
    
    if echo "$RESPONSE" | grep -q '"id"'; then
        echo "  ✓ Created job $i"
    else
        echo "  ✗ Failed to create job $i: $RESPONSE"
    fi
    
    sleep 0.2
done

echo ""
echo -e "${GREEN}✓ Created $NUM_JOBS jobs for tenant: $TENANT_ID${NC}"
echo ""
echo "View them in the dashboard at: http://localhost:3000"
echo "Or check the database:"
echo "  kubectl exec -it postgres-0 -n jobqueue -- \\"
echo "    psql -U postgres jobqueue -c \\"
echo "    \"SELECT id, status, payload FROM jobs WHERE tenant_id='${TENANT_ID}' ORDER BY created_at DESC LIMIT 10;\""
echo ""

# Cleanup port-forward if we started it
if [ ! -z "$PF_PID" ]; then
    echo "Stopping port-forward..."
    kill $PF_PID 2>/dev/null || true
fi
