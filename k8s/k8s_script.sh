#!/bin/bash

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if file exists
check_file() {
    if [ ! -f "$1" ]; then
        print_error "Required file not found: $1"
        print_error "Please ensure the file exists before running this script."
        exit 1
    fi
}

# Function to check if command exists
check_command() {
    if ! command -v "$1" &> /dev/null; then
        print_error "Required command '$1' not found"
        print_error "Please install $1 before running this script."
        exit 1
    fi
}

print_status "Starting Kubernetes deployment script..."

# Check required commands
print_status "Checking required tools..."
check_command "minikube"
check_command "kubectl"
print_success "All required tools found"

# Check if dev.env file exists
DEV_ENV_FILE="../dev.env"
print_status "Checking for dev.env file..."
check_file "$DEV_ENV_FILE"
print_success "dev.env file found"

# Load environment variables from dev.env
print_status "Loading environment variables from dev.env..."
export $(grep -v '^#' "$DEV_ENV_FILE" | xargs)

# Check if required environment variables are set
if [ -z "$gitlab_secret_username" ]; then
    print_error "gitlab_secret_username not found in $DEV_ENV_FILE"
    print_error "Please add: gitlab_secret_username=your_gitlab_username"
    exit 1
fi

if [ -z "$gitlab_secret_password" ]; then
    print_error "gitlab_secret_password not found in $DEV_ENV_FILE"
    print_error "Please add: gitlab_secret_password=your_gitlab_token"
    exit 1
fi

print_success "Environment variables loaded successfully"

# Start minikube
print_status "Starting minikube..."
if ! minikube start; then
    print_error "Failed to start minikube"
    print_error "Please check your minikube installation and try again"
    exit 1
fi
print_success "Minikube started successfully"

# Wait for kubectl to be ready
print_status "Waiting for kubectl to be ready..."
if ! kubectl cluster-info &> /dev/null; then
    print_error "kubectl is not ready. Please check your minikube setup."
    exit 1
fi
print_success "kubectl is ready"

# Create GitLab registry secret
print_status "Creating GitLab registry secret..."
if kubectl get secret gitlab-registry-access-g2t5 &> /dev/null; then
    print_warning "GitLab registry secret already exists, deleting old one..."
    kubectl delete secret gitlab-registry-access-g2t5
fi

# Create environment variables secret from dev.env
print_status "Creating environment variables secret from dev.env..."
if kubectl get secret env-vars-secret &> /dev/null; then
    print_warning "Environment variables secret already exists, deleting old one..."
    kubectl delete secret env-vars-secret
fi

if ! kubectl create secret generic env-vars-secret \
    --from-env-file="$DEV_ENV_FILE" \
    --dry-run=client -o yaml > temp-secret.yaml; then
    print_error "Failed to create environment variables secret YAML"
    print_error "Please check your $DEV_ENV_FILE file"
    exit 1
fi

if ! kubectl apply -f temp-secret.yaml; then
    print_error "Failed to apply environment variables secret"
    print_error "Please check your $DEV_ENV_FILE file"
    rm -f temp-secret.yaml
    exit 1
fi

rm -f temp-secret.yaml
print_success "Environment variables secret created successfully"

if ! kubectl create secret docker-registry gitlab-registry-access-g2t5 \
    --docker-server=registry.gitlab.com \
    --docker-username="$gitlab_secret_username" \
    --docker-password="$gitlab_secret_password"; then
    print_error "Failed to create GitLab registry secret"
    print_error "Please check your GitLab credentials in $DEV_ENV_FILE"
    exit 1
fi
print_success "GitLab registry secret created successfully"

# Apply configmap
print_status "Applying configmap..."
check_file "configmap.yaml"
if ! kubectl apply -f configmap.yaml; then
    print_error "Failed to apply configmap.yaml"
    exit 1
fi
print_success "Configmap applied successfully"

# Start RabbitMQ deployment first
print_status "Deploying RabbitMQ..."
check_file "rabbitmq.yaml"
if ! kubectl apply -f rabbitmq.yaml; then
    print_error "Failed to deploy RabbitMQ"
    exit 1
fi
print_success "RabbitMQ deployment started"

# Wait for RabbitMQ to be ready
print_status "Waiting for RabbitMQ to be ready (this may take a few minutes)..."
if ! kubectl wait --for=condition=available --timeout=300s deployment/rabbitmq; then
    print_error "RabbitMQ deployment failed to become ready within 5 minutes"
    print_error "Check RabbitMQ pod logs: kubectl logs -l app=rabbitmq"
    exit 1
fi
print_success "RabbitMQ is ready"

# Deploy API Gateway first (before other services)
print_status "Deploying API Gateway..."
check_file "api-gateway.yaml"
if ! kubectl apply -f api-gateway.yaml; then
    print_error "Failed to deploy API Gateway"
    exit 1
fi
print_success "API Gateway deployment started"

# Wait for API Gateway to be ready
print_status "Waiting for API Gateway to be ready..."
if ! kubectl wait --for=condition=available --timeout=300s deployment/api-gateway; then
    print_error "API Gateway deployment failed to become ready within 5 minutes"
    print_error "Check API Gateway pod logs: kubectl logs -l app=api-gateway"
    exit 1
fi

# Wait for API Gateway service to be ready
print_status "Waiting for API Gateway service to be ready..."
if ! kubectl wait --for=condition=ready --timeout=60s pod -l app=api-gateway; then
    print_warning "API Gateway pods may not be fully ready, but continuing..."
fi
print_success "API Gateway is ready"

# Get API Gateway URL from minikube (handling blocking call)
print_status "Starting minikube service tunnel for API Gateway..."

# Start the service in background and capture output to file
minikube service api-gateway --url > api_gateway_url.temp 2>&1 &
TUNNEL_PID=$!

# Wait for tunnel to establish
print_status "Waiting for tunnel to establish..."
sleep 15

# Read the URL from the file
API_GATEWAY_URL=""
if [ -f "api_gateway_url.temp" ]; then
    API_GATEWAY_URL=$(grep -o 'http://[^[:space:]]*' api_gateway_url.temp | head -n 1)
fi

if [ -z "$API_GATEWAY_URL" ]; then
    print_error "Failed to get API Gateway URL from minikube tunnel"
    print_error "Make sure API Gateway service is running"
    rm -f api_gateway_url.temp
    exit 1
fi

print_success "API Gateway URL: $API_GATEWAY_URL"
print_warning "Minikube tunnel is running in background (PID: $TUNNEL_PID) - keep this terminal open"

# Check if ConfigMap already exists and compare URLs
RESTART_FRONTEND=0
if kubectl get configmap frontend-config &> /dev/null; then
    print_status "Checking if API Gateway URL changed..."
    
    # Get old URL from ConfigMap
    OLD_API_URL=$(kubectl get configmap frontend-config -o jsonpath='{.data.API_GATEWAY_URL}' 2>/dev/null || echo "")
    
    if [ "$OLD_API_URL" != "$API_GATEWAY_URL" ]; then
        print_warning "API Gateway URL changed!"
        print_warning "Old URL: $OLD_API_URL"
        print_warning "New URL: $API_GATEWAY_URL"
        print_status "Will restart frontend after updating ConfigMap"
        RESTART_FRONTEND=1
    else
        print_success "API Gateway URL unchanged, no frontend restart needed"
        RESTART_FRONTEND=0
    fi
    
    # Delete old ConfigMap
    kubectl delete configmap frontend-config
else
    RESTART_FRONTEND=1
fi

# Create frontend ConfigMap with API Gateway URL
print_status "Creating frontend configuration with API Gateway URL..."
if ! kubectl create configmap frontend-config \
    --from-literal=API_GATEWAY_URL="$API_GATEWAY_URL" \
    --dry-run=client -o yaml > temp-frontend-config.yaml; then
    print_error "Failed to create frontend config YAML"
    exit 1
fi

if ! kubectl apply -f temp-frontend-config.yaml; then
    print_error "Failed to apply frontend config"
    rm -f temp-frontend-config.yaml
    exit 1
fi

rm -f temp-frontend-config.yaml
print_success "Frontend configuration created with API Gateway URL"

# Update the main configmap with API Gateway URL
print_status "Updating main configmap with API Gateway URL..."
if ! kubectl patch configmap env-vars-configmap \
    --patch "{\"data\":{\"api_gateway_url\":\"$API_GATEWAY_URL\"}}"; then
    print_warning "Failed to update main configmap, but continuing..."
else
    print_success "Main configmap updated with API Gateway URL"
fi

# Deploy other services
print_status "Deploying other services..."

# Files to skip (already processed)
SKIP_FILES=("configmap.yaml" "secret.yaml" "rabbitmq.yaml" "api-gateway.yaml" "k8s_script.sh" ".gitignore")

# Function to check if file should be skipped
should_skip() {
    local file="$1"
    for skip_file in "${SKIP_FILES[@]}"; do
        if [ "$file" = "$skip_file" ]; then
            return 0
        fi
    done
    return 1
}

# Deploy all other YAML files
deployed_count=0
for file in *.yaml; do
    # Skip if file doesn't exist (no yaml files case)
    [ ! -f "$file" ] && continue
    
    # Skip already processed files
    if should_skip "$file"; then
        continue
    fi
    
    print_status "Applying $file..."
    if ! kubectl apply -f "$file"; then
        print_error "Failed to apply $file"
        exit 1
    fi
    print_success "$file applied successfully"
    ((deployed_count++))
done

if [ $deployed_count -eq 0 ]; then
    print_warning "No additional YAML files found to deploy"
else
    print_success "Deployed $deployed_count additional services"
fi

# Wait for all deployments to be ready
print_status "Waiting for all deployments to be ready..."
if ! kubectl wait --for=condition=available --timeout=300s deployment --all; then
    print_warning "Some deployments may not be ready yet"
    print_status "Check deployment status with: kubectl get deployments"
    print_status "Check pod status with: kubectl get pods"
else
    print_success "All deployments are ready"
fi

# Restart frontend if API Gateway URL changed
if [ "$RESTART_FRONTEND" -eq 1 ]; then
    print_status "Restarting frontend to apply new API Gateway URL..."
    if ! kubectl rollout restart deployment/frontend; then
        print_warning "Failed to restart frontend, but continuing..."
    else
        print_status "Waiting for frontend rollout to complete..."
        if ! kubectl rollout status deployment/frontend --timeout=120s; then
            print_warning "Frontend rollout did not complete in time"
        else
            print_success "Frontend restarted successfully"
        fi
    fi
fi

# Show final status
print_status "Deployment Summary:"
echo "===================="
kubectl get deployments
echo ""
kubectl get services
echo ""
kubectl get pods

print_success "Kubernetes deployment completed successfully!"
print_status "You can check the status with:"
echo "  kubectl get pods"
echo "  kubectl get services"
echo "  kubectl logs -l app=<service-name>"

# Show access information
print_status "Service Access Information:"
echo "============================"
minikube service list

# Show frontend access information
print_status "Frontend Configuration:"
echo "========================"
echo "API Gateway URL: $API_GATEWAY_URL"
echo "Frontend will automatically use this URL for API calls"
echo ""
echo "To access the frontend dashboard:"
FRONTEND_URL=$(minikube service frontend --url)
echo "Frontend URL: $FRONTEND_URL"

print_success "Script completed successfully!"