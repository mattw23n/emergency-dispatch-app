# 🚀 Quick Start Guide - Emergency Services Dashboard

## Step 1: Start All Services

Open a terminal in the project root and run:

```bash
docker-compose up --build
```

Wait for all services to start (you should see messages like "Successfully connected to RabbitMQ" and "Database tables created successfully").

## Step 2: Access the Dashboard

Open your web browser and navigate to:

```
http://localhost:8080
```

The dashboard will load automatically!

## Step 3: Verify Services Are Healthy

On the dashboard, you should see:

- ✅ All service nodes showing green borders (healthy)
- ✅ Service Health Status cards showing "Healthy"
- ✅ Events-Manager node highlighted in orange/gold

If any services show as unhealthy (red), check the Docker logs:

```bash
docker-compose logs <service-name>
```

## Step 4: Test the Event Orchestration

Click the test buttons at the bottom of the dashboard:

### Test 1: Emergency Scenario
1. Click **"🚨 Trigger Emergency (Triage)"**
2. Watch the Event Timeline for:
   - Triage emergency detection
   - Ambulance dispatch by Events-Manager
   - Notification alert

### Test 2: Full Workflow
1. Click **"🎬 Simulate Full Emergency Workflow"**
2. Watch the complete 24-second workflow:
   - 0s: Critical vitals detected
   - 1s: Emergency alert sent
   - 2s: Ambulance assigned
   - 8s: Patient onboard
   - 18s: Arrived at hospital
   - 19s: Billing initiated
   - 24s: Process complete

### Test 3: Ambulance Dispatch
1. Click **"🚑 Request Ambulance (Dispatch)"**
2. Watch the automated workflow:
   - 5s delay → Patient onboard
   - Vitals monitoring every 2 seconds
   - 15s → Arrived at hospital
   - Billing process triggered

## Step 5: Monitor Real Events (Optional)

To see real events flowing through the system:

### Option A: Use RabbitMQ Management UI

1. Open http://localhost:15672
2. Login: `guest` / `guest`
3. Click "Queues" tab
4. Publish test messages to queues

### Option B: Use Service APIs

Send a POST request to trigger real events:

```bash
# Example: Trigger triage event (if endpoint exists)
curl -X POST http://localhost:8080/api/v1/triage/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "P123",
    "vitals": {
      "heart_rate": 140,
      "spo2": 88
    }
  }'
```

## Troubleshooting

### Services Won't Start

```bash
# Check Docker is running
docker --version

# Check for port conflicts
netstat -ano | findstr "8080"
netstat -ano | findstr "5672"
netstat -ano | findstr "3306"

# Restart Docker Desktop
```

### Dashboard Shows All Services as Unhealthy

```bash
# Check if containers are running
docker ps

# Check api-gateway logs
docker-compose logs api-gateway

# Restart the gateway
docker-compose restart api-gateway
```

### CORS Errors in Browser Console

1. Clear browser cache (Ctrl+Shift+Delete)
2. Make sure you're using `http://localhost:8080` (not file://)
3. Check nginx configuration includes CORS headers
4. Restart api-gateway:
   ```bash
   docker-compose restart api-gateway
   ```

### Events-Manager Not Working

```bash
# Check RabbitMQ is healthy
docker-compose ps rabbitmq

# Check Events-Manager logs
docker-compose logs -f events-manager

# Verify RabbitMQ queues exist
# Open http://localhost:15672 → Queues tab
# Should see:
#   - events-manager.q.triage-actionable
#   - events-manager.q.dispatch-status
#   - events-manager.q.billing-status
```

## Architecture Overview

```
┌──────────────────────────────────────────────────┐
│           Browser (localhost:8080)               │
│              Dashboard.html                      │
└────────────────┬─────────────────────────────────┘
                 │ HTTP Requests
                 ▼
┌──────────────────────────────────────────────────┐
│         API Gateway (nginx:80)                   │
│  - Serves dashboard.html                         │
│  - Proxies /api/v1/* to services                 │
│  - Adds CORS headers                             │
└────────────────┬─────────────────────────────────┘
                 │ Proxied API calls
                 ▼
┌──────────────────────────────────────────────────┐
│              Microservices                       │
│  - Wearable (5000)                               │
│  - Triage (5001)                                 │
│  - Dispatch (8081) ←─┐                          │
│  - Notification (8000)│                          │
│  - Billings (5100) ←─┤                          │
│  - Insurance (5200)   │                          │
└───────────────────────┼──────────────────────────┘
                        │ AMQP Messages
                        ▼
┌──────────────────────────────────────────────────┐
│       Events-Manager (8011) ⭐                   │
│  Orchestrates event flow between services        │
└────────────────┬─────────────────────────────────┘
                 │ Publishes/Consumes
                 ▼
┌──────────────────────────────────────────────────┐
│          RabbitMQ (5672)                         │
│  Message broker for event-driven architecture    │
└──────────────────────────────────────────────────┘
```

## What to Expect

### Service Health Panel
- 9 service cards with real-time health indicators
- Auto-refresh every 10 seconds
- Click any service node to see details

### Architecture Diagram
- Visual topology with connection lines
- Events-Manager highlighted in orange
- Color-coded health status (green/red/orange)
- Interactive service nodes

### Event Timeline
- Real-time event log
- Color-coded by event type:
  - 🟣 Purple: Triage
  - 🔵 Blue: Dispatch
  - 🟢 Green: Billing
  - 🟠 Orange: Notification
- Timestamps for each event
- Auto-scrolling (newest at top)

## Next Steps

1. ✅ **Start services**: `docker-compose up --build`
2. ✅ **Open dashboard**: http://localhost:8080
3. ✅ **Test workflows**: Click the test buttons
4. ✅ **Monitor events**: Watch the timeline
5. 📖 **Read full docs**: See `frontend/README.md`

## Support

If you encounter issues:

1. Check the detailed README: `frontend/README.md`
2. View Docker logs: `docker-compose logs <service-name>`
3. Check RabbitMQ: http://localhost:15672
4. Verify containers: `docker ps`

Happy monitoring! 🚑✨
