#!/bin/bash
set -e  # Exit on any error

ARGO_NS="argocd"

echo "==================================================================="
echo "        Emergency Response System - ArgoCD Setup Script"
echo "==================================================================="
echo
echo "This script will:"
echo "1. Install ArgoCD in your Kubernetes cluster"
echo "2. Deploy the root application (App of Apps pattern)"
echo "3. Deploy the emergency application (all microservices)"
echo "4. Provide access credentials and URLs"
echo
echo "Prerequisites:"
echo "- Minikube is running"
echo "- kubectl is configured and working"
echo "- All required secrets/configmaps are created in k8s cluster"
echo

read -p "Press Enter to continue..."
echo

# Check if kubectl is working
echo "[1/6] Checking Kubernetes connectivity..."
if ! kubectl cluster-info >/dev/null 2>&1; then
    echo "ERROR: kubectl is not working or cluster is not accessible"
    echo "Please ensure minikube is running and kubectl is configured"
    read -p "Press Enter to exit..."
    exit 1
fi
echo "✓ Kubernetes cluster is accessible"

# Check if minikube is running
echo "[2/6] Verifying minikube status..."
if ! minikube status | grep -q "Running"; then
    echo "WARNING: Minikube may not be running properly"
    echo "Attempting to start minikube..."
    if ! minikube start; then
        echo "ERROR: Failed to start minikube"
        read -p "Press Enter to exit..."
        exit 1
    fi
fi
echo "✓ Minikube is running"

# Create ArgoCD namespace and install ArgoCD
echo "[3/6] Installing ArgoCD..."
if ! kubectl get namespace "$ARGO_NS" >/dev/null 2>&1; then
    echo "Creating ArgoCD namespace..."
    kubectl create namespace "$ARGO_NS"
else
    echo "ArgoCD namespace already exists"
fi

echo "Installing ArgoCD manifests..."
if ! kubectl apply -n "$ARGO_NS" -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml; then
    echo "ERROR: Failed to install ArgoCD"
    read -p "Press Enter to exit..."
    exit 1
fi
echo "✓ ArgoCD installation initiated"

# Configure RBAC permissions
echo "Configuring RBAC permissions..."
kubectl create clusterrolebinding argocd-application-controller-cluster-admin \
    --clusterrole=cluster-admin \
    --serviceaccount="$ARGO_NS":argocd-application-controller >/dev/null 2>&1 || true
kubectl create clusterrolebinding argocd-server-cluster-admin \
    --clusterrole=cluster-admin \
    --serviceaccount="$ARGO_NS":argocd-server >/dev/null 2>&1 || true
echo "✓ RBAC permissions configured"

# Wait for ArgoCD to be ready
echo "[4/6] Waiting for ArgoCD components to be ready..."
echo "This may take a few minutes..."

while ! kubectl wait --for=condition=Ready pods -l app.kubernetes.io/name=argocd-server -n "$ARGO_NS" --timeout=300s >/dev/null 2>&1; do
    echo "Still waiting for ArgoCD server to be ready..."
    sleep 10
done
echo "✓ ArgoCD server is ready"

# Deploy applications
echo "[5/6] Deploying applications..."
echo "Deploying root application (App of Apps)..."
if ! kubectl apply -f root-app.yaml; then
    echo "ERROR: Failed to deploy root application"
    read -p "Press Enter to exit..."
    exit 1
fi

echo "Deploying emergency response application..."
if ! kubectl apply -f emergency-app.yaml; then
    echo "ERROR: Failed to deploy emergency application"
    read -p "Press Enter to exit..."
    exit 1
fi
echo "✓ Applications deployed successfully"

# Setup access instructions
echo "[6/6] Setup complete! Providing access instructions..."

echo
echo "==================================================================="
echo "                   SETUP COMPLETE - ACCESS INSTRUCTIONS"
echo "==================================================================="
echo "Your entire Emergency Response System is now deploying via ArgoCD."
echo "ArgoCD will synchronize all applications from the k8s repository."
echo

echo "--- HOW TO ACCESS ARGOCD WEB UI ---"
echo
echo "1. Open a NEW terminal window"
echo
echo "2. Run this command to get the ARGOCD Web UI:"
echo "   minikube service argocd-server -n %ARGO_NS% --url"
echo
echo "3. Keep that terminal window open (don't close it)"
echo
echo
echo "4. Accept the self-signed certificate warning"
echo

echo "--- LOGIN CREDENTIALS ---"
echo
echo "Username: admin"
echo
echo "To get the password, run this command in another terminal:"
echo "kubectl get secret argocd-initial-admin-secret -n $ARGO_NS -o jsonpath=\"{.data.password}\" | base64 -d"
echo
echo "Or on macOS:"
echo "kubectl get secret argocd-initial-admin-secret -n $ARGO_NS -o jsonpath=\"{.data.password}\" | base64 -D"
echo

echo "--- APPLICATION ACCESS ---"
echo
echo "Your Emergency Response System API Gateway will be available at:"
MINIKUBE_IP=$(minikube ip 2>/dev/null || echo "MINIKUBE_IP")
echo "   Main URL: http://$MINIKUBE_IP:30080"
echo "   Dashboard: http://$MINIKUBE_IP:30080/"
echo "   API Base: http://$MINIKUBE_IP:30080/api/v1/"
echo

echo "--- USEFUL MONITORING COMMANDS ---"
echo
echo "Check ArgoCD applications status:"
echo "   kubectl get applications -n $ARGO_NS"
echo
echo "Check all your application pods:"
echo "   kubectl get pods"
echo
echo "Check services:"
echo "   kubectl get svc"
echo
echo "View ArgoCD application details:"
echo "   kubectl describe application emergency-app -n $ARGO_NS"
echo "   kubectl describe application root-app -n $ARGO_NS"
echo

echo "--- NEXT STEPS ---"
echo
echo "1. Follow the instructions above to access ArgoCD Web UI"
echo "2. Login with admin credentials"
echo "3. Change the admin password immediately (recommended)"
echo "4. Monitor your applications in the ArgoCD dashboard"
echo "5. Verify all services are healthy and synced"
echo "6. Access your Emergency Response System API"
echo

echo "--- TROUBLESHOOTING ---"
echo
echo "If applications show as \"OutOfSync\" or \"Unhealthy\":"
echo "1. Check pod logs: kubectl logs -l app=SERVICE_NAME"
echo "2. Verify secrets/configmaps exist: kubectl get secrets,configmaps"
echo "3. Force sync in ArgoCD UI"
echo
echo "If port forwarding stops working:"
echo "1. Stop the port-forward terminal (Ctrl+C)"
echo "2. Re-run the port-forward command"
echo

echo "==================================================================="
echo "    IMPORTANT: Keep the port-forward terminal open to access ArgoCD"
echo "==================================================================="
echo

read -p "Press Enter to finish..."