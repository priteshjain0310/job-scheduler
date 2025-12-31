#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE="jobqueue"
REGISTRY="localhost:5001"
VERSION="local"

# Functions
print_step() {
    echo -e "${BLUE}==>${NC} ${GREEN}$1${NC}"
}

print_warning() {
    echo -e "${YELLOW}WARNING:${NC} $1"
}

print_error() {
    echo -e "${RED}ERROR:${NC} $1"
}

check_command() {
    if ! command -v $1 &> /dev/null; then
        print_error "$1 is not installed. Please install it first."
        exit 1
    fi
}

# Detect Kubernetes environment
detect_k8s_env() {
    if command -v minikube &> /dev/null && minikube status &> /dev/null; then
        echo "minikube"
    elif command -v kind &> /dev/null && kind get clusters 2>/dev/null | grep -q "kind"; then
        echo "kind"
    elif kubectl cluster-info &> /dev/null; then
        echo "other"
    else
        echo "none"
    fi
}

# Start Minikube
start_minikube() {
    print_step "Starting Minikube..."
    
    # Check if Minikube is running and healthy
    if minikube status 2>/dev/null | grep -q "Running"; then
        # Check if API server is responding
        if ! kubectl cluster-info &>/dev/null; then
            print_warning "Minikube is running but API server is not responding. Restarting..."
            minikube delete
            minikube start --cpus=4 --memory=8192 --driver=docker
        else
            print_warning "Minikube is already running and healthy"
        fi
    else
        print_step "Starting new Minikube cluster..."
        minikube start --cpus=4 --memory=8192 --driver=docker
    fi
    
    # Wait for cluster to be ready
    print_step "Waiting for Kubernetes to be ready..."
    kubectl wait --for=condition=Ready nodes --all --timeout=300s
    
    # Enable addons (with error handling)
    print_step "Enabling Minikube addons..."
    minikube addons enable metrics-server || print_warning "Failed to enable metrics-server (will continue)"
    minikube addons enable ingress || print_warning "Failed to enable ingress (will continue)"
    
    # Use Minikube's Docker daemon
    eval $(minikube docker-env)
}

# Start Kind
start_kind() {
    print_step "Starting Kind cluster..."
    
    if kind get clusters 2>/dev/null | grep -q "kind"; then
        print_warning "Kind cluster already exists"
    else
        cat <<EOF | kind create cluster --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 30080
    hostPort: 8080
    protocol: TCP
  - containerPort: 30443
    hostPort: 8443
    protocol: TCP
EOF
    fi
    
    # Install metrics-server for HPA
    print_step "Installing metrics-server..."
    kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
    
    # Patch metrics-server for Kind
    kubectl patch -n kube-system deployment metrics-server --type=json \
        -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
}

# Build Docker images
build_images() {
    print_step "Building Docker images..."
    
    # API
    print_step "Building API image..."
    docker build -f Dockerfile.api -t ${REGISTRY}/jobqueue-api:${VERSION} .
    
    # Worker
    print_step "Building Worker image..."
    docker build -f Dockerfile.worker -t ${REGISTRY}/jobqueue-worker:${VERSION} .
    
    # Reaper
    print_step "Building Reaper image..."
    docker build -f Dockerfile.reaper -t ${REGISTRY}/jobqueue-reaper:${VERSION} .
    
    # Dashboard
    print_step "Building Dashboard image..."
    docker build -f dashboard/Dockerfile -t ${REGISTRY}/jobqueue-dashboard:${VERSION} ./dashboard
    
    print_step "✓ All images built successfully"
}

# Create namespace
create_namespace() {
    print_step "Creating namespace..."
    kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
}

# Create secrets
create_secrets() {
    print_step "Creating secrets..."
    
    # Generate random password if not set
    DB_PASSWORD=${DB_PASSWORD:-$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)}
    API_SECRET=${API_SECRET:-$(openssl rand -base64 32)}
    
    kubectl create secret generic jobqueue-secret \
        --from-literal=DATABASE_URL="postgresql+asyncpg://postgres:${DB_PASSWORD}@postgres:5432/jobqueue" \
        --from-literal=API_SECRET_KEY="${API_SECRET}" \
        --from-literal=POSTGRES_USER="postgres" \
        --from-literal=POSTGRES_PASSWORD="${DB_PASSWORD}" \
        --namespace=${NAMESPACE} \
        --dry-run=client -o yaml | kubectl apply -f -
    
    print_step "✓ Secrets created"
    echo -e "${YELLOW}Database Password:${NC} ${DB_PASSWORD}"
    echo -e "${YELLOW}API Secret Key:${NC} ${API_SECRET}"
}

# Update image references in manifests
update_manifests() {
    print_step "Updating image references..." >&2
    
    # Create temporary directory for modified manifests
    TMP_DIR=$(mktemp -d)
    
    # Copy manifests
    cp -r k8s/* "${TMP_DIR}/"
    
    # Update image references
    for file in "${TMP_DIR}"/api.yaml "${TMP_DIR}"/worker.yaml "${TMP_DIR}"/reaper.yaml "${TMP_DIR}"/migration-job.yaml "${TMP_DIR}"/dashboard.yaml; do
        if [ -f "$file" ]; then
            sed -i.bak "s|image: jobqueue-\(.*\):latest|image: ${REGISTRY}/jobqueue-\1:${VERSION}|g" "$file"
            sed -i.bak "s|imagePullPolicy: IfNotPresent|imagePullPolicy: Never|g" "$file"
            rm -f "$file.bak"
        fi
    done
    
    echo "${TMP_DIR}"
}

# Deploy to Kubernetes
deploy_k8s() {
    print_step "Deploying to Kubernetes..."
    
    MANIFEST_DIR="$1"
    
    # Deploy in order
    kubectl apply -f "${MANIFEST_DIR}/namespace.yaml"
    kubectl apply -f "${MANIFEST_DIR}/configmap.yaml"
    kubectl apply -f "${MANIFEST_DIR}/postgres.yaml"
    
    # Wait for PostgreSQL
    print_step "Waiting for PostgreSQL to be ready..."
    kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=postgres \
        -n ${NAMESPACE} --timeout=300s
    
    # Run migrations
    print_step "Running database migrations..."
    kubectl apply -f "${MANIFEST_DIR}/migration-job.yaml"
    kubectl wait --for=condition=complete job/jobqueue-migration \
        -n ${NAMESPACE} --timeout=300s || {
        print_error "Migration failed. Check logs:"
        kubectl logs job/jobqueue-migration -n ${NAMESPACE}
        exit 1
    }
    
    # Deploy services
    kubectl apply -f "${MANIFEST_DIR}/api.yaml"
    kubectl apply -f "${MANIFEST_DIR}/worker.yaml"
    kubectl apply -f "${MANIFEST_DIR}/reaper.yaml"
    kubectl apply -f "${MANIFEST_DIR}/dashboard.yaml"
    
    # Optional: Deploy monitoring and security
    if [ -f "${MANIFEST_DIR}/pdb.yaml" ]; then
        kubectl apply -f "${MANIFEST_DIR}/pdb.yaml"
    fi
    
    if [ -f "${MANIFEST_DIR}/servicemonitor.yaml" ]; then
        kubectl apply -f "${MANIFEST_DIR}/servicemonitor.yaml" 2>/dev/null || \
            print_warning "ServiceMonitor not applied (Prometheus Operator not installed)"
    fi
    
    print_step "✓ Deployment complete"
}

# Wait for deployments
wait_for_deployments() {
    print_step "Waiting for deployments to be ready..."
    
    kubectl rollout status deployment/jobqueue-api -n ${NAMESPACE} --timeout=300s
    kubectl rollout status deployment/jobqueue-worker -n ${NAMESPACE} --timeout=300s
    kubectl rollout status deployment/jobqueue-reaper -n ${NAMESPACE} --timeout=300s
    kubectl rollout status deployment/jobqueue-dashboard -n ${NAMESPACE} --timeout=300s
    
    print_step "✓ All deployments ready"
}

# Show access information
show_access_info() {
    print_step "Deployment Information"
    echo ""
    
    # Get pod status
    echo -e "${GREEN}Pods:${NC}"
    kubectl get pods -n ${NAMESPACE}
    echo ""
    
    # Get services
    echo -e "${GREEN}Services:${NC}"
    kubectl get svc -n ${NAMESPACE}
    echo ""
    
    # Get HPA
    echo -e "${GREEN}Horizontal Pod Autoscalers:${NC}"
    kubectl get hpa -n ${NAMESPACE}
    echo ""
    
    # Access instructions
    echo -e "${GREEN}Access API:${NC}"
    
    K8S_ENV=$(detect_k8s_env)
    if [ "$K8S_ENV" = "minikube" ]; then
        MINIKUBE_IP=$(minikube ip)
        NODE_PORT=$(kubectl get svc jobqueue-api-service -n ${NAMESPACE} -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
        if [ "$NODE_PORT" != "N/A" ]; then
            echo "  URL: http://${MINIKUBE_IP}:${NODE_PORT}"
        fi
        echo "  Or run: kubectl port-forward svc/jobqueue-api-service 8000:80 -n ${NAMESPACE}"
    elif [ "$K8S_ENV" = "kind" ]; then
        echo "  Run: kubectl port-forward svc/jobqueue-api-service 8000:80 -n ${NAMESPACE}"
        echo "  Then access: http://localhost:8000"
    else
        echo "  Run: kubectl port-forward svc/jobqueue-api-service 8000:80 -n ${NAMESPACE}"
        echo "  Then access: http://localhost:8000"
    fi
    
    echo ""
    echo -e "${GREEN}Access Dashboard:${NC}"
    if [ "$K8S_ENV" = "minikube" ]; then
        echo "  Run: kubectl port-forward svc/jobqueue-dashboard-service 3000:80 -n ${NAMESPACE}"
    else
        echo "  Run: kubectl port-forward svc/jobqueue-dashboard-service 3000:80 -n ${NAMESPACE}"
    fi
    echo "  Then access: http://localhost:3000"
    
    echo ""
    echo -e "${GREEN}Useful Commands:${NC}"
    echo "  View API logs:       kubectl logs -f deployment/jobqueue-api -n ${NAMESPACE}"
    echo "  View worker logs:    kubectl logs -f deployment/jobqueue-worker -n ${NAMESPACE}"
    echo "  View dashboard logs: kubectl logs -f deployment/jobqueue-dashboard -n ${NAMESPACE}"
    echo "  Scale workers:       kubectl scale deployment jobqueue-worker --replicas=5 -n ${NAMESPACE}"
    echo "  Watch HPA:           kubectl get hpa -n ${NAMESPACE} --watch"
    echo "  Delete all:          kubectl delete namespace ${NAMESPACE}"
}

# Cleanup function
cleanup() {
    print_step "Cleaning up..."
    kubectl delete namespace ${NAMESPACE} --ignore-not-found=true
    print_step "✓ Cleanup complete"
}

# Main script
main() {
    echo -e "${BLUE}"
    echo "╔═══════════════════════════════════════════════════════╗"
    echo "║   Job Scheduler - Local Kubernetes Deployment        ║"
    echo "╚═══════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    
    # Parse arguments
    CLEAN=false
    SKIP_BUILD=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --clean)
                CLEAN=true
                shift
                ;;
            --skip-build)
                SKIP_BUILD=true
                shift
                ;;
            --help)
                echo "Usage: $0 [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --clean       Delete existing deployment before deploying"
                echo "  --skip-build  Skip Docker image building"
                echo "  --help        Show this help message"
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done
    
    # Check prerequisites
    print_step "Checking prerequisites..."
    check_command kubectl
    check_command docker
    
    # Detect Kubernetes environment
    K8S_ENV=$(detect_k8s_env)
    
    if [ "$K8S_ENV" = "none" ]; then
        print_warning "No Kubernetes cluster detected"
        
        if command -v minikube &> /dev/null; then
            print_step "Starting Minikube..."
            start_minikube
            K8S_ENV="minikube"
        elif command -v kind &> /dev/null; then
            print_step "Starting Kind..."
            start_kind
            K8S_ENV="kind"
        else
            print_error "Please install Minikube or Kind first"
            echo "  Minikube: https://minikube.sigs.k8s.io/docs/start/"
            echo "  Kind: https://kind.sigs.k8s.io/docs/user/quick-start/"
            exit 1
        fi
    else
        print_step "Using existing Kubernetes cluster: ${K8S_ENV}"
    fi
    
    # Clean if requested
    if [ "$CLEAN" = true ]; then
        cleanup
    fi
    
    # Build images
    if [ "$SKIP_BUILD" = false ]; then
        if [ "$K8S_ENV" = "minikube" ]; then
            eval $(minikube docker-env)
        fi
        build_images
    else
        print_warning "Skipping image build"
    fi
    
    # Create namespace and secrets
    create_namespace
    create_secrets
    
    # Update manifests
    MANIFEST_DIR=$(update_manifests)
    
    # Deploy
    deploy_k8s ${MANIFEST_DIR}
    
    # Wait for deployments
    wait_for_deployments
    
    # Cleanup temp directory
    rm -rf ${MANIFEST_DIR}
    
    # Show access information
    echo ""
    show_access_info
    
    echo ""
    print_step "🎉 Deployment successful!"
}

# Run main function
main "$@"
