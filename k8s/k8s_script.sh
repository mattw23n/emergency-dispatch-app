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
if ! source "$DEV_ENV_FILE"; then
    print_error "Failed to load environment variables from $DEV_ENV_FILE"
    exit 1
fi

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

if ! kubectl create secret docker-registry gitlab-registry-access-g2t5 \
    --docker-server=registry.gitlab.com \
    --docker-username="$gitlab_secret_username" \
    --docker-password="$gitlab_secret_password"; then
    print_error "Failed to create GitLab registry secret"
    print_error "Please check your GitLab credentials in $DEV_ENV_FILE"
    exit 1
fi
print_success "GitLab registry secret created successfully"

# Create environment variables secret from dev.env
print_status "Creating environment variables secret from dev.env..."
if kubectl get secret env-vars-secret &> /dev/null; then
    print_warning "Environment variables secret already exists, deleting old one..."
    kubectl delete secret env-vars-secret
fi

if ! kubectl create secret generic env-vars-secret \
    --from-env-file="$DEV_ENV_FILE" \
    --dry-run=client -o yaml | kubectl apply -f -; then
    print_error "Failed to create environment variables secret"
    print_error "Please check your $DEV_ENV_FILE file"
    exit 1
fi
print_success "Environment variables secret created successfully"


# Apply configmap
print_status "Applying configmap..."
check_file "configmap.yaml"
if ! kubectl apply -f configmap.yaml; then
    print_error "Failed to apply configmap.yaml"
    print_error "Please check the configmap.yaml file for syntax errors"
    exit 1
fi
print_success "Configmap applied successfully"

# # Apply secret
# print_status "Applying secret..."
# check_file "secret.yaml"
# if ! kubectl apply -f secret.yaml; then
#     print_error "Failed to apply secret.yaml"
#     print_error "Please check the secret.yaml file for syntax errors"
#     exit 1
# fi
# print_success "Secret applied successfully"

# Start RabbitMQ deployment first
print_status "Deploying RabbitMQ..."
check_file "rabbitmq.yaml"
if ! kubectl apply -f rabbitmq.yaml; then
    print_error "Failed to deploy RabbitMQ"
    print_error "Please check the rabbitmq.yaml file for syntax errors"
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

# Deploy other services
print_status "Deploying other services..."

# Files to skip (already processed)
SKIP_FILES=("configmap.yaml" "secret.yaml" "rabbitmq.yaml" "k8s_script.sh" ".gitignore")

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
        print_error "Please check $file for syntax errors"
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

print_success "Script completed successfully!"