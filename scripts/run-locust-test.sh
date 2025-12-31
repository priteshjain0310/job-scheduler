#!/bin/bash
set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}🦗 Locust Load Testing${NC}"
echo ""

# Check if Locust is installed
if ! command -v locust &> /dev/null; then
    echo -e "${YELLOW}Locust not installed. Installing...${NC}"
    pip install locust
    echo ""
fi

# Configuration
MODE=${1:-"web"}
USERS=${2:-100}
SPAWN_RATE=${3:-10}
DURATION=${4:-"5m"}

echo -e "${BLUE}Configuration:${NC}"
echo "  Mode: $MODE"
echo "  Users: $USERS"
echo "  Spawn Rate: $SPAWN_RATE users/sec"
echo "  Duration: $DURATION"
echo ""

# Check if API is accessible
API_URL="http://localhost:8000"
if ! curl -s ${API_URL}/health > /dev/null 2>&1; then
    echo "Starting port-forward to API..."
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

# Create results directory
mkdir -p results

# Run Locust based on mode
case $MODE in
    web)
        echo -e "${BLUE}Starting Locust Web UI...${NC}"
        echo ""
        echo "Open browser: http://localhost:8089"
        echo ""
        locust -f tests/load/locustfile.py --host=${API_URL}
        ;;
    
    headless)
        echo -e "${BLUE}Running headless load test...${NC}"
        echo ""
        locust -f tests/load/locustfile.py \
            --host=${API_URL} \
            --headless \
            --users ${USERS} \
            --spawn-rate ${SPAWN_RATE} \
            --run-time ${DURATION} \
            --csv=results/load-test-$(date +%Y%m%d-%H%M%S) \
            --html=results/load-test-$(date +%Y%m%d-%H%M%S).html
        
        echo ""
        echo -e "${GREEN}✓ Test complete!${NC}"
        echo ""
        echo "Results saved in results/"
        ls -lh results/ | tail -5
        ;;
    
    normal)
        echo -e "${BLUE}Testing normal traffic (JobSchedulerUser)...${NC}"
        echo ""
        locust -f tests/load/locustfile.py \
            --host=${API_URL} \
            --headless \
            --users ${USERS} \
            --spawn-rate ${SPAWN_RATE} \
            --run-time ${DURATION} \
            JobSchedulerUser
        ;;
    
    burst)
        echo -e "${BLUE}Testing burst traffic (BurstSubmissionUser)...${NC}"
        echo ""
        locust -f tests/load/locustfile.py \
            --host=${API_URL} \
            --headless \
            --users ${USERS} \
            --spawn-rate ${SPAWN_RATE} \
            --run-time ${DURATION} \
            BurstSubmissionUser
        ;;
    
    idempotency)
        echo -e "${BLUE}Testing idempotency (IdempotencyTestUser)...${NC}"
        echo ""
        locust -f tests/load/locustfile.py \
            --host=${API_URL} \
            --headless \
            --users ${USERS} \
            --spawn-rate ${SPAWN_RATE} \
            --run-time ${DURATION} \
            IdempotencyTestUser
        ;;
    
    *)
        echo "Unknown mode: $MODE"
        echo ""
        echo "Usage: $0 [mode] [users] [spawn_rate] [duration]"
        echo ""
        echo "Modes:"
        echo "  web          - Start Web UI (default)"
        echo "  headless     - Run headless test with reports"
        echo "  normal       - Test normal traffic pattern"
        echo "  burst        - Test burst traffic pattern"
        echo "  idempotency  - Test idempotency guarantees"
        echo ""
        echo "Examples:"
        echo "  $0 web                    # Start Web UI"
        echo "  $0 headless 200 20 10m    # 200 users, 20/sec spawn, 10 minutes"
        echo "  $0 burst 100 10 5m        # Burst test with 100 users"
        exit 1
        ;;
esac

# Cleanup port-forward if we started it
if [ ! -z "$PF_PID" ]; then
    kill $PF_PID 2>/dev/null || true
fi
