@echo off

SETLOCAL ENABLEDELAYEDEXPANSION
SET ARGO_NS=argocd

ECHO ===================================================================
ECHO        Emergency Response System - ArgoCD Setup Script
ECHO ===================================================================
ECHO.
ECHO This script will:
ECHO 1. Install ArgoCD in your Kubernetes cluster
ECHO 2. Deploy the root application (App of Apps pattern)
ECHO 3. Deploy the emergency application (all microservices)
ECHO 4. Provide access credentials and URLs
ECHO.
ECHO Prerequisites:
ECHO - Minikube is running
ECHO - kubectl is configured and working
ECHO - All required secrets/configmaps are created in k8s cluster
ECHO.

PAUSE
ECHO.

REM Check if kubectl is working
ECHO [1/6] Checking Kubernetes connectivity...
kubectl cluster-info >nul 2>&1
IF ERRORLEVEL 1 (
    ECHO ERROR: kubectl is not working or cluster is not accessible
    ECHO Please ensure minikube is running and kubectl is configured
    PAUSE
    EXIT /B 1
)
ECHO ✓ Kubernetes cluster is accessible

REM Check if minikube is running
ECHO [2/6] Verifying minikube status...
minikube status | findstr "Running" >nul
IF ERRORLEVEL 1 (
    ECHO WARNING: Minikube may not be running properly
    ECHO Attempting to start minikube...
    minikube start
    IF ERRORLEVEL 1 (
        ECHO ERROR: Failed to start minikube
        PAUSE
        EXIT /B 1
    )
)
ECHO ✓ Minikube is running

REM Create ArgoCD namespace and install ArgoCD
ECHO [3/6] Installing ArgoCD...
kubectl get namespace %ARGO_NS% >nul 2>&1
IF ERRORLEVEL 1 (
    ECHO Creating ArgoCD namespace...
    kubectl create namespace %ARGO_NS%
) ELSE (
    ECHO ArgoCD namespace already exists
)

ECHO Installing ArgoCD manifests...
kubectl apply -n %ARGO_NS% -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
IF ERRORLEVEL 1 (
    ECHO ERROR: Failed to install ArgoCD
    PAUSE
    EXIT /B 1
)
ECHO ✓ ArgoCD installation initiated

REM Wait for ArgoCD to be ready
ECHO [4/6] Waiting for ArgoCD components to be ready...
ECHO This may take a few minutes...

:WAIT_ARGOCD
kubectl wait --for=condition=Ready pods -l app.kubernetes.io/name=argocd-server -n %ARGO_NS% --timeout=300s >nul 2>&1
IF ERRORLEVEL 1 (
    ECHO Still waiting for ArgoCD server to be ready...
    timeout /t 10 /nobreak >nul
    GOTO WAIT_ARGOCD
)
ECHO ✓ ArgoCD server is ready

REM Deploy applications
ECHO [5/6] Deploying applications...
ECHO Deploying root application (App of Apps)...
kubectl apply -f root-app.yaml
IF ERRORLEVEL 1 (
    ECHO ERROR: Failed to deploy root application
    PAUSE
    EXIT /B 1
)

ECHO Deploying emergency response application...
kubectl apply -f emergency-app.yaml
IF ERRORLEVEL 1 (
    ECHO ERROR: Failed to deploy emergency application
    PAUSE
    EXIT /B 1
)
ECHO ✓ Applications deployed successfully

REM Setup access instructions
ECHO [6/6] Setup complete! Providing access instructions...

ECHO.
ECHO ===================================================================
ECHO                   SETUP COMPLETE - ACCESS INSTRUCTIONS
ECHO ===================================================================
ECHO Your entire Emergency Response System is now deploying via ArgoCD.
ECHO ArgoCD will synchronize all applications from the k8s repository.
ECHO.

ECHO --- HOW TO ACCESS ARGOCD WEB UI ---
ECHO.
ECHO 1. Open a NEW terminal/command prompt window
ECHO.
ECHO 2. Run this command to get the ARGOCD Web UI:
ECHO   minikube service argocd-server -n %ARGO_NS% --url
ECHO.
ECHO 3. Keep that terminal window open (don't close it)
ECHO.
ECHO 4. Accept the self-signed certificate warning
ECHO.

ECHO --- LOGIN CREDENTIALS ---
ECHO.
ECHO Username: admin
ECHO.
ECHO To get the password, run this command in another terminal:
ECHO kubectl get secret argocd-initial-admin-secret -n %ARGO_NS% -o jsonpath="{.data.password}" ^| base64 -d
ECHO.
ECHO Or if you have PowerShell:
ECHO powershell -Command "kubectl get secret argocd-initial-admin-secret -n %ARGO_NS% -o jsonpath='{.data.password}' | ForEach-Object { [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($_)) }"
ECHO.

ECHO --- APPLICATION ACCESS ---
ECHO.
ECHO Your Emergency Response System API Gateway will be available at:
FOR /F %%i IN ('minikube ip 2^>nul') DO SET MINIKUBE_IP=%%i
ECHO    Main URL: http://!MINIKUBE_IP!:30080
ECHO    Dashboard: http://!MINIKUBE_IP!:30080/
ECHO    API Base: http://!MINIKUBE_IP!:30080/api/v1/
ECHO.
PAUSE