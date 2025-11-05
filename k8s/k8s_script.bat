@echo off
setlocal enabledelayedexpansion

REM Colors for Windows CMD (using echo with special characters)
set "RED=[91m"
set "GREEN=[92m"
set "YELLOW=[93m"
set "BLUE=[94m"
set "NC=[0m"

echo %BLUE%[INFO]%NC% Starting Kubernetes deployment script...

REM Check if minikube is installed
minikube version >nul 2>&1
if %errorlevel% neq 0 (
    echo %RED%[ERROR]%NC% minikube not found. Please install minikube first.
    exit /b 1
)

REM Check if kubectl is installed
kubectl version --client >nul 2>&1
if %errorlevel% neq 0 (
    echo %RED%[ERROR]%NC% kubectl not found. Please install kubectl first.
    exit /b 1
)

echo %GREEN%[SUCCESS]%NC% All required tools found

REM Check if dev.env file exists
if not exist "..\dev.env" (
    echo %RED%[ERROR]%NC% Required file not found: ..\dev.env
    echo %RED%[ERROR]%NC% Please ensure the file exists before running this script.
    exit /b 1
)

echo %GREEN%[SUCCESS]%NC% dev.env file found

REM Load environment variables from dev.env
echo %BLUE%[INFO]%NC% Loading environment variables from dev.env...
for /f "usebackq tokens=1,2 delims==" %%a in ("..\dev.env") do (
    if "%%a"=="gitlab_secret_username" set "gitlab_secret_username=%%b"
    if "%%a"=="gitlab_secret_password" set "gitlab_secret_password=%%b"
)

REM Check if required environment variables are set
if "%gitlab_secret_username%"=="" (
    echo %RED%[ERROR]%NC% gitlab_secret_username not found in ..\dev.env
    echo %RED%[ERROR]%NC% Please add: gitlab_secret_username=your_gitlab_username
    exit /b 1
)

if "%gitlab_secret_password%"=="" (
    echo %RED%[ERROR]%NC% gitlab_secret_password not found in ..\dev.env
    echo %RED%[ERROR]%NC% Please add: gitlab_secret_password=your_gitlab_token
    exit /b 1
)

echo %GREEN%[SUCCESS]%NC% Environment variables loaded successfully

REM Start minikube
echo %BLUE%[INFO]%NC% Starting minikube...
minikube start
if %errorlevel% neq 0 (
    echo %RED%[ERROR]%NC% Failed to start minikube
    echo %RED%[ERROR]%NC% Please check your minikube installation and try again
    exit /b 1
)
echo %GREEN%[SUCCESS]%NC% Minikube started successfully

REM Wait for kubectl to be ready
echo %BLUE%[INFO]%NC% Waiting for kubectl to be ready...
kubectl cluster-info >nul 2>&1
if %errorlevel% neq 0 (
    echo %RED%[ERROR]%NC% kubectl is not ready. Please check your minikube setup.
    exit /b 1
)
echo %GREEN%[SUCCESS]%NC% kubectl is ready

REM Create GitLab registry secret
echo %BLUE%[INFO]%NC% Creating GitLab registry secret...
kubectl get secret gitlab-registry-access-g2t5 >nul 2>&1
if %errorlevel% equ 0 (
    echo %YELLOW%[WARNING]%NC% GitLab registry secret already exists, deleting old one...
    kubectl delete secret gitlab-registry-access-g2t5
)

REM Create environment variables secret from dev.env
echo %BLUE%[INFO]%NC% Creating environment variables secret from dev.env...
kubectl get secret env-vars-secret >nul 2>&1
if %errorlevel% equ 0 (
    echo %YELLOW%[WARNING]%NC% Environment variables secret already exists, deleting old one...
    kubectl delete secret env-vars-secret
)

kubectl create secret generic env-vars-secret --from-env-file="..\dev.env" --dry-run=client -o yaml > temp-secret.yaml
if %errorlevel% neq 0 (
    echo %RED%[ERROR]%NC% Failed to create environment variables secret YAML
    echo %RED%[ERROR]%NC% Please check your ..\dev.env file
    exit /b 1
)

kubectl apply -f temp-secret.yaml
if %errorlevel% neq 0 (
    echo %RED%[ERROR]%NC% Failed to apply environment variables secret
    echo %RED%[ERROR]%NC% Please check your ..\dev.env file
    del temp-secret.yaml
    exit /b 1
)

del temp-secret.yaml
echo %GREEN%[SUCCESS]%NC% Environment variables secret created successfully

kubectl create secret docker-registry gitlab-registry-access-g2t5 --docker-server=registry.gitlab.com --docker-username=%gitlab_secret_username% --docker-password=%gitlab_secret_password%
if %errorlevel% neq 0 (
    echo %RED%[ERROR]%NC% Failed to create GitLab registry secret
    echo %RED%[ERROR]%NC% Please check your GitLab credentials in ..\dev.env
    exit /b 1
)
echo %GREEN%[SUCCESS]%NC% GitLab registry secret created successfully

REM Apply configmap
echo %BLUE%[INFO]%NC% Applying configmap...
if not exist "configmap.yaml" (
    echo %RED%[ERROR]%NC% Required file not found: configmap.yaml
    exit /b 1
)
kubectl apply -f configmap.yaml
if %errorlevel% neq 0 (
    echo %RED%[ERROR]%NC% Failed to apply configmap.yaml
    exit /b 1
)
echo %GREEN%[SUCCESS]%NC% Configmap applied successfully

@REM REM Apply secret
@REM echo %BLUE%[INFO]%NC% Applying secret...
@REM if not exist "secret.yaml" (
@REM     echo %RED%[ERROR]%NC% Required file not found: secret.yaml
@REM     exit /b 1
@REM )
@REM kubectl apply -f secret.yaml
@REM if %errorlevel% neq 0 (
@REM     echo %RED%[ERROR]%NC% Failed to apply secret.yaml
@REM     exit /b 1
@REM )
@REM echo %GREEN%[SUCCESS]%NC% Secret applied successfully

REM Start RabbitMQ deployment first
echo %BLUE%[INFO]%NC% Deploying RabbitMQ...
if not exist "rabbitmq.yaml" (
    echo %RED%[ERROR]%NC% Required file not found: rabbitmq.yaml
    exit /b 1
)
kubectl apply -f rabbitmq.yaml
if %errorlevel% neq 0 (
    echo %RED%[ERROR]%NC% Failed to deploy RabbitMQ
    exit /b 1
)
echo %GREEN%[SUCCESS]%NC% RabbitMQ deployment started

REM Wait for RabbitMQ to be ready
echo %BLUE%[INFO]%NC% Waiting for RabbitMQ to be ready (this may take a few minutes)...
kubectl wait --for=condition=available --timeout=300s deployment/rabbitmq
if %errorlevel% neq 0 (
    echo %RED%[ERROR]%NC% RabbitMQ deployment failed to become ready within 5 minutes
    echo %RED%[ERROR]%NC% Check RabbitMQ pod logs: kubectl logs -l app=rabbitmq
    exit /b 1
)
echo %GREEN%[SUCCESS]%NC% RabbitMQ is ready

REM Deploy other services
echo %BLUE%[INFO]%NC% Deploying other services...

set deployed_count=0
for %%f in (*.yaml) do (
    if not "%%f"=="configmap.yaml" if not "%%f"=="secret.yaml" if not "%%f"=="rabbitmq.yaml" if not "%%f"==".gitignore" (
        echo %BLUE%[INFO]%NC% Applying %%f...
        kubectl apply -f "%%f"
        if !errorlevel! neq 0 (
            echo %RED%[ERROR]%NC% Failed to apply %%f
            exit /b 1
        )
        echo %GREEN%[SUCCESS]%NC% %%f applied successfully
        set /a deployed_count+=1
    )
)

if %deployed_count%==0 (
    echo %YELLOW%[WARNING]%NC% No additional YAML files found to deploy
) else (
    echo %GREEN%[SUCCESS]%NC% Deployed %deployed_count% additional services
)

REM Wait for all deployments to be ready
echo %BLUE%[INFO]%NC% Waiting for all deployments to be ready...
kubectl wait --for=condition=available --timeout=300s deployment --all
if %errorlevel% neq 0 (
    echo %YELLOW%[WARNING]%NC% Some deployments may not be ready yet
    echo %BLUE%[INFO]%NC% Check deployment status with: kubectl get deployments
    echo %BLUE%[INFO]%NC% Check pod status with: kubectl get pods
) else (
    echo %GREEN%[SUCCESS]%NC% All deployments are ready
)

REM Show final status
echo %BLUE%[INFO]%NC% Deployment Summary:
echo ====================
kubectl get deployments
echo.
kubectl get services
echo.
kubectl get pods

echo %GREEN%[SUCCESS]%NC% Kubernetes deployment completed successfully!
echo %BLUE%[INFO]%NC% You can check the status with:
echo   kubectl get pods
echo   kubectl get services
echo   kubectl logs -l app=^<service-name^>

REM Show access information
echo %BLUE%[INFO]%NC% Service Access Information:
echo ============================
minikube service list

echo %GREEN%[SUCCESS]%NC% Script completed successfully!
pause