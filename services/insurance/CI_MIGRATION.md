# GitLab CI Updates for Node.js Insurance Service

## Summary of Changes

The insurance service has been migrated from **Python/Flask** to **Node.js/Express**. The CI/CD pipeline has been updated accordingly.

---

## 🔄 Changes Made

### 1. `.gitlab-ci.yml` - CI/CD Pipeline

#### ❌ Removed (Python-specific):
- `flake8` stage - Python linter
- `pylint` stage - Python static analysis

#### ✅ Added (JavaScript-specific):
- `eslint` stage - JavaScript linter and static analysis
  - Uses `node:20-slim` image
  - Installs ESLint via npm
  - Generates JSON and text reports
  - Set to `allow_failure: true` (won't block pipeline on warnings)

### 2. `.eslintrc.json` - ESLint Configuration (NEW)

Created ESLint config with:
- Node.js environment settings
- ES2021 standards
- Recommended rules
- Custom rules:
  - 2-space indentation
  - Double quotes
  - Semicolons required
  - Console.log allowed (for server logging)

### 3. `ci/compose.test.yaml` - Test Docker Compose

**Updated Architecture**:
```
┌─────────────┐
│   MySQL     │ (Database)
└──────┬──────┘
       │
       ↓
┌─────────────┐
│  Insurance  │ (Node.js app running)
│   Service   │ http://insurance-service:5200
└──────┬──────┘
       │
       ↓
┌─────────────┐
│   Pytest    │ (Python tests via HTTP)
│   Runner    │ Tests the running service
└─────────────┘
```

**Key Changes**:
- Added `insurance-service` container running Node.js app
- Service has healthcheck: `wget http://localhost:5200/health`
- Pytest container depends on both MySQL and insurance-service
- Tests run against live HTTP service (not in-process)
- Environment variables for DB connection and service URL

### 4. `ci/Dockerfile.test` - Test Container

**Before**: Installed Python dependencies and ran tests in same container
**After**: Installs only pytest dependencies, tests external service

**Changes**:
- Removed: Node.js source code copying
- Removed: Node.js dependency installation
- Added: Wait for both MySQL and insurance-service to be healthy
- Tests now run via HTTP against running service

### 5. `Dockerfile` - Main Service

**Added**: `wget` for healthcheck support
```dockerfile
RUN apk add --no-cache wget
```

This allows the healthcheck to work:
```yaml
healthcheck:
  test: ["CMD", "wget", "--spider", "-q", "http://localhost:5200/health"]
```

---

## 🚀 How It Works Now

### Test Flow

1. **MySQL starts** → waits for healthy
2. **Insurance service starts** → waits for MySQL → exposes port 5200
3. **Pytest container starts** → waits for both services
4. **Python tests make HTTP requests** to `http://insurance-service:5200`
5. **Node.js app responds** with JSON
6. **Tests validate** responses match expected format

### Static Analysis Flow

1. **ESLint stage** runs:
   - Installs Node.js dependencies
   - Installs ESLint
   - Analyzes `src/` directory
   - Generates reports
   - Allows failures (won't block merge)

---

## 📋 What Stays the Same

### Unchanged Stages:
- ✅ **Release** - Docker image building and pushing
- ✅ **Deploy** - ECS deployment
- ✅ **Integration Tests** - Still runs `compose.test.yaml`

### Test Strategy:
- ✅ Still uses **pytest** (Python test framework)
- ✅ Tests via **HTTP requests** (language-agnostic)
- ✅ Same test cases, updated for Node.js API responses

---

## 🧪 Running Tests Locally

### Option 1: Via Docker Compose (Recommended)
```bash
cd services/insurance
docker-compose -f ci/compose.test.yaml up --abort-on-container-exit
```

This will:
1. Start MySQL
2. Start Node.js insurance service
3. Run Python tests against the service
4. Exit with test results

### Option 2: Manual Testing
```bash
# Terminal 1: Start MySQL
docker run -d -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=root \
  -e MYSQL_DATABASE=cs302DB \
  mysql:9.4.0

# Terminal 2: Start Node.js service
cd services/insurance
npm install
export DB_HOST=localhost
export DB_PORT=3306
export DB_USER=root
export DB_PASSWORD=root
export DB_NAME=cs302DB
npm start

# Terminal 3: Run tests
pip install -r ci/requirements.test.txt
export INSURANCE_SERVICE_URL=http://localhost:5200
pytest tests/ -v
```

---

## 🔍 Static Analysis Locally

```bash
cd services/insurance

# Install ESLint
npm install --save-dev eslint

# Run ESLint
npx eslint src/

# Auto-fix issues
npx eslint src/ --fix
```

---

## 📦 Updated Dependencies

### Python Test Dependencies (`ci/requirements.test.txt`)
```
pytest==8.4.1
pytest-dependency==0.6.0
requests==2.31.0              # NEW - for HTTP calls
mysql-connector-python==8.2.0  # NEW - for DB access
```

### Node.js Dependencies (`package.json`)
No changes needed - already has all required dependencies.

---

## ✅ Verification Checklist

Before pushing to GitLab:

- [ ] ESLint config created (`.eslintrc.json`)
- [ ] `wget` added to Dockerfile
- [ ] `compose.test.yaml` has 3 services (mysql, insurance-service, pytest)
- [ ] Pytest Dockerfile.test waits for both dependencies
- [ ] Python tests use HTTP requests (not Flask test client)
- [ ] Environment variables set in compose.test.yaml
- [ ] Test locally with `docker-compose -f ci/compose.test.yaml up`

---

## 🎯 Expected GitLab CI Behavior

### Pipeline Stages:

1. **Static Analysis** ✓
   - ESLint runs
   - Warnings allowed (won't fail pipeline)
   - Reports saved as artifacts

2. **Test** ✓
   - Docker Compose starts all services
   - Node.js app runs on port 5200
   - Python tests execute via HTTP
   - Tests must pass for pipeline to continue

3. **Release** ✓
   - Builds Docker image
   - Pushes to registry

4. **Deploy** ✓
   - Deploys to ECS (on main branch only)

---

## 🐛 Troubleshooting

### Tests Can't Connect to Service
**Problem**: `Connection refused` errors in pytest
**Solution**: Check insurance-service healthcheck is passing
```bash
docker-compose -f ci/compose.test.yaml ps
# Ensure insurance-service shows (healthy)
```

### ESLint Errors in CI
**Problem**: ESLint finds issues in code
**Solution**: Run locally and fix issues
```bash
npx eslint src/ --fix
```

### Database Connection Errors
**Problem**: Node.js app can't connect to MySQL
**Solution**: Verify environment variables in compose.test.yaml
```bash
docker-compose -f ci/compose.test.yaml logs insurance-service
```

---

## 📝 Summary

**Old Stack**: Python Flask + Flask test client + Python linters
**New Stack**: Node.js Express + HTTP tests (Python) + ESLint

**Key Insight**: Tests in Python, app in JavaScript - works perfectly via HTTP! 🎉
