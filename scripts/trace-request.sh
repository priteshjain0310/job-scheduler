#!/bin/bash
set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}🔍 Request Tracer${NC}"
echo ""

# Get trace ID from argument or search for recent one
TRACE_ID=$1

if [ -z "$TRACE_ID" ]; then
    echo "Searching for recent trace IDs..."
    TRACE_ID=$(kubectl logs -l app.kubernetes.io/name=jobqueue-api -n jobqueue --tail=100 | \
        grep -o '"trace_id":"[^"]*"' | tail -1 | cut -d'"' -f4)
    
    if [ -z "$TRACE_ID" ]; then
        echo "No trace IDs found in recent logs."
        echo ""
        echo "Usage: $0 [trace_id]"
        echo ""
        echo "Or create a job first to generate traces."
        exit 1
    fi
    
    echo -e "${YELLOW}Found recent trace ID: ${TRACE_ID}${NC}"
else
    echo -e "${YELLOW}Searching for trace ID: ${TRACE_ID}${NC}"
fi

echo ""
echo -e "${BLUE}=== API Logs ===${NC}"
kubectl logs -l app.kubernetes.io/name=jobqueue-api -n jobqueue --tail=1000 | grep "$TRACE_ID" || echo "No API logs found"

echo ""
echo -e "${BLUE}=== Worker Logs ===${NC}"
kubectl logs -l app.kubernetes.io/name=jobqueue-worker -n jobqueue --tail=1000 | grep "$TRACE_ID" || echo "No worker logs found"

echo ""
echo -e "${BLUE}=== Reaper Logs ===${NC}"
kubectl logs -l app.kubernetes.io/name=jobqueue-reaper -n jobqueue --tail=1000 | grep "$TRACE_ID" || echo "No reaper logs found"

echo ""
echo -e "${GREEN}✓ Trace search complete${NC}"
echo ""
echo "To view in Jaeger UI:"
echo "  1. Deploy Jaeger: kubectl apply -f k8s/jaeger.yaml"
echo "  2. Port-forward: kubectl port-forward svc/jaeger-query -n jobqueue 16686:16686"
echo "  3. Open: http://localhost:16686"
echo "  4. Search for trace ID: ${TRACE_ID}"
