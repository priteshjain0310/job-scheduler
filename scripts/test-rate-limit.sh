#!/bin/bash
set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}🚦 Rate Limiting Test${NC}"
echo ""

# Configuration
TENANT_ID=${1:-"rate-test-tenant"}
REQUESTS=${2:-150}  # More than default limit of 100/min
API_URL="http://localhost:8000"

echo -e "${YELLOW}Configuration:${NC}"
echo "  Tenant ID: $TENANT_ID"
echo "  Total Requests: $REQUESTS"
echo "  Rate Limit: 100 requests/minute (default)"
echo "  Burst Capacity: 200 requests (2x rate)"
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
  -d "{\"tenant_id\": \"${TENANT_ID}\", \"api_key\": \"test-key\"}" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

if [ -z "$TOKEN" ]; then
    echo "Failed to get auth token"
    [ ! -z "$PF_PID" ] && kill $PF_PID 2>/dev/null || true
    exit 1
fi

echo -e "${GREEN}✓ Got auth token${NC}"
echo ""

# Test rate limiting
echo -e "${BLUE}Sending $REQUESTS requests...${NC}"
echo ""

SUCCESS_COUNT=0
RATE_LIMITED_COUNT=0
ERROR_COUNT=0

START_TIME=$(date +%s)

for i in $(seq 1 $REQUESTS); do
    # Make request
    RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${API_URL}/v1/jobs" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Idempotency-Key: rate-test-${i}-$(date +%s%N)" \
      -d "{
        \"tenant_id\": \"${TENANT_ID}\",
        \"payload\": {
          \"job_type\": \"echo\",
          \"data\": {\"message\": \"Rate test ${i}\"}
        }
      }")
    
    HTTP_CODE=$(echo "$RESPONSE" | tail -1)
    BODY=$(echo "$RESPONSE" | head -n -1)
    
    if [ "$HTTP_CODE" = "201" ]; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        echo -e "${GREEN}✓${NC} Request $i: Success (201)"
    elif [ "$HTTP_CODE" = "429" ]; then
        RATE_LIMITED_COUNT=$((RATE_LIMITED_COUNT + 1))
        RETRY_AFTER=$(echo "$BODY" | grep -o '"Retry after [^"]*"' || echo "")
        echo -e "${YELLOW}⏸${NC}  Request $i: Rate Limited (429) $RETRY_AFTER"
    else
        ERROR_COUNT=$((ERROR_COUNT + 1))
        echo -e "${RED}✗${NC} Request $i: Error ($HTTP_CODE)"
    fi
    
    # Small delay to see rate limiting in action
    sleep 0.01
done

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
echo -e "${BLUE}=== Results ===${NC}"
echo "  Total Requests: $REQUESTS"
echo "  Duration: ${DURATION}s"
echo "  Rate: $((REQUESTS / DURATION)) requests/sec"
echo ""
echo -e "${GREEN}  ✓ Successful: $SUCCESS_COUNT${NC}"
echo -e "${YELLOW}  ⏸  Rate Limited: $RATE_LIMITED_COUNT${NC}"
echo -e "${RED}  ✗ Errors: $ERROR_COUNT${NC}"
echo ""

# Analysis
if [ $RATE_LIMITED_COUNT -gt 0 ]; then
    echo -e "${GREEN}✓ Rate limiting is working!${NC}"
    echo ""
    echo "Expected behavior:"
    echo "  - First ~200 requests succeed (burst capacity)"
    echo "  - Subsequent requests get rate limited (429)"
    echo "  - Rate limit: 100 requests/minute"
    echo "  - Tokens refill at ~1.67/second"
else
    echo -e "${YELLOW}⚠ No rate limiting detected${NC}"
    echo ""
    echo "Possible reasons:"
    echo "  - Requests sent too slowly (under rate limit)"
    echo "  - Rate limit not configured"
    echo "  - Try increasing request count: $0 $TENANT_ID 300"
fi

echo ""
echo "To test different tenants:"
echo "  $0 tenant-1 200"
echo "  $0 tenant-2 200"
echo ""
echo "To configure rate limit, set environment variable:"
echo "  RATE_LIMIT_REQUESTS_PER_MINUTE=50"

# Cleanup port-forward if we started it
if [ ! -z "$PF_PID" ]; then
    kill $PF_PID 2>/dev/null || true
fi
