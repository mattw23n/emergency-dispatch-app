# SHERS: Smart Healthcare Emergency Response System

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![JavaScript](https://img.shields.io/badge/Language-JavaScript-yellow)
![DevOps](https://img.shields.io/badge/DevOps-GitOps-orange)

## 📖 Project Overview

**SHERS** (Smart Healthcare Emergency Response System), also known as the **Emergency Dispatch App**, is a microservices-based framework designed to address and solve critical delays in emergency healthcare response.

Current emergency systems often rely on fragmented, manual handoffs (from triage to dispatch to billing) and subjective eyewitness accounts. This leads to lost time during the **"Golden Hour"**—the critical period immediately following a traumatic incident where timely intervention significantly improves patient survival rates.

SHERS leverages real-time **IoT sensor data** to automate detection, intelligent dispatching, and billing. By utilizing an **event-driven architecture**, the system ensures seamless communication between services, drastically reducing response times and optimizing resource allocation.

---

## 🏗 System Architecture

The application is built on an event-driven microservices architecture using a **RabbitMQ** topic exchange to decouple services. The infrastructure ensures data survival during restarts via durable queues and handles communication through a publish-subscribe model.

### Core Components

| Component | Description |
| :--- | :--- |
| **Event Backbone** | **RabbitMQ AMQP**. Handles communication between services with durable queues. |
| **API Gateway** | **NGINX**. Serves as the unified entry point for the frontend dashboard and external API calls. |
| **Frontend** | An **Admin Dashboard** for visualizing live scenarios and event streams. |

### Microservices

Most services are implemented in **Python**, with the exception of the Insurance service (**JavaScript**).

*   **Mock Wearable Service:** Simulates IoT devices; validates and normalizes vitals (e.g., heart rate) and acts as the initial data publisher.
*   **Triage Service:** Consumes wearable data to classify patient status (`Normal`, `Abnormal`, or `Emergency`) based on rule thresholds.
*   **Events Manager Service (Orchestrator):** The core coordinator. It deduplicates incidents, triggers notifications, commands dispatch workflows, and initiates billing upon incident closure.
*   **Dispatch Service:** Manages the ambulance lifecycle. It assigns units, calculates ETAs via **Google Maps API**, and streams location updates.
*   **Notification Service:** A decoupled service that sends SMS/Email updates to the Next-of-Kin (NOK) via **Amazon SNS**.
*   **Billing Service:** Acts as a Saga orchestrator for payments. It manages the transaction lifecycle (`PENDING` -> `PAID`/`FAILED`) using **Stripe API** and **Amazon RDS**.
*   **Insurance Service:** An internal helper service (JS) accessed via synchronous HTTP to verify coverage.
*   **Events Stream Service:** Implements a CQRS-style read model to stream events to the admin dashboard for visualization.

---

## 🔄 Operational Scenarios

The system handles incidents through three distinct workflow phases:

### Scenario 01: Detection & Triage
1.  **Wearable:** Detects high heart rate/fall and publishes raw data.
2.  **Triage:** Analyzes data and flags an `Emergency` status.
3.  **Notification:** Events Manager receives status and commands Notification Service to alert the Next-of-Kin (NOK).

### Scenario 02: Dispatch & Routing
1.  **Trigger:** Events Manager triggers the Dispatch workflow following an emergency flag.
2.  **Routing:** Dispatch Service locates the nearest ambulance/hospital using the **Google Maps API**.
3.  **Updates:** Provides real-time status updates (`Enroute`, `Onboard`, `Arrived`) back to the event stream.

### Scenario 03: Payment & Settlement
1.  **Initiation:** Once the patient arrives at the hospital, Events Manager initiates billing.
2.  **Verification:** Billing Service calls Insurance Service to verify coverage.
3.  **Processing:** Processes payment via **Stripe API** and commits to **Amazon RDS**.
4.  **Compensation:** If payment fails, a compensation workflow is triggered to rollback the transaction.

---

## 🚀 DevOps Practices

This project adopts **"DevOps as a Lifestyle,"** focusing heavily on automation, security, and observability.

### Infrastructure as Code (IaC) & Containerization
*   **Docker:** Every microservice has a dedicated `Dockerfile`.
*   **Docker Compose:** Mirrors the production environment for local development.
*   **Kubernetes (Minikube):** Handles production orchestration. Manifests define the infrastructure to ensure reproducibility.
*   **Automation Scripts:** Custom Bash/Batch scripts automate repository pulling, K8s setup, and ArgoCD initialization.

### CI/CD Pipeline
Each service implements a robust pipeline triggered by Git commits:

1.  **Static Analysis:**
    *   Python: `Flake8`, `Pylint`
    *   JavaScript: `ESLint`
2.  **DevSecOps (Security Tests):**
    *   **Semgrep:** SAST tool for finding bugs and security anti-patterns (e.g., root user execution).
    *   **Trivy:** Scans dependencies for vulnerabilities and misconfigurations.
    *   **Gitleaks:** Scans for exposed secrets and API keys.
3.  **Testing:**
    *   Custom Unit Tests: `Pytest` (Python), `Jest` (JS).
    *   Integration Tests: Run against runtime databases and RabbitMQ containers.
4.  **Release:**
    *   Docker images are built and pushed to GitLab Container Registry with `:latest` tag and commit SHA.

### Deployment (GitOps)
*   **Tooling:** **ArgoCD** + **GitLab CI**.
*   **Workflow:** CI pipeline updates the K8s manifest with the new image SHA -> ArgoCD detects the Git change -> Automatically triggers a cluster rollout.
*   **Scaling:** Horizontal Pod Autoscalers (**HPA**) configured for 75% target CPU utilization.
*   **Strategies:** Uses `Rollout` for zero downtime and `Replace` where appropriate.

### Observability
*   **Health Checks:** Dedicated endpoints for all services.
*   **Monitoring:** Logs tracked via CI/CD pipeline and Kubernetes dashboard for deployment health and resource usage.

---

## 🛠 Tech Stack

**Languages:**
*   Python 🐍
*   JavaScript 🟨

**Frameworks & APIs:**
*   RabbitMQ (AMQP)
*   NGINX
*   Google Maps API
*   Stripe API
*   Amazon SNS
*   Amazon RDS

**DevOps Tools:**
*   Docker & Docker Compose
*   Kubernetes (Minikube)
*   ArgoCD
*   GitLab CI
*   Semgrep / Trivy / Gitleaks