Hospital + Dispatch combined service

This Flask app provides dynamic hospital management and ambulance dispatch with real-time search.

## Features
- **Dynamic hospital database** (SQLAlchemy + SQLite by default)
- **Real-time nearest hospital search** (Haversine distance)
- **Smart hospital selection** (distance + capacity + severity weighting)
- **Google Maps API integration** (optional, for realistic routes/ETA)
- **Full CRUD operations** for hospitals

## Endpoints

### Health
- `GET /` - health check

### Hospital Search
- `POST /hospital/nearest` - find nearest hospital by distance only
  - Body: `{"location": {"lat": 51.51, "lng": -0.12}}`
- `POST /hospital/best` - smart hospital selection (capacity + severity)
  - Body: `{"location": {"lat": 51.51, "lng": -0.12}, "severity": 2}`

### Hospital Management (CRUD)
- `GET /hospital/list` - list all hospitals
- `POST /hospital/add` - add a new hospital
  - Body: `{"id": "hosp-x", "name": "Name", "lat": 51.5, "lng": -0.1, "capacity": 10}`
- `PUT /hospital/<id>` - update a hospital
  - Body: `{"name": "New Name", "capacity": 15}`
- `DELETE /hospital/<id>` - delete a hospital

### Dispatch
- `POST /dispatch/dispatch` - dispatch an ambulance
  - Body: `{"patient_location": {"lat": 51.51, "lng": -0.12}, "hospital_id": "optional", "use_google": false, "google_api_key": "optional"}`
  - Auto-selects best hospital if `hospital_id` not provided
  - Uses Google Maps for route/ETA if `use_google=true` and API key is set

## Setup

### Option 1: Docker Compose (Recommended)

1. **Copy environment file (optional):**
```cmd
copy .env.example .env
```
Edit `.env` and add your Google Maps API key if needed.

2. **Start the service:**
```cmd
docker-compose up -d
```

3. **Check status:**
```cmd
docker-compose ps
docker-compose logs -f dispatch-service
```

4. **Access the API:**
- Service: http://localhost:8080
- Health check: http://localhost:8080/

5. **Stop the service:**
```cmd
docker-compose down
```

To use MySQL instead of SQLite, uncomment the `mysql` service section in `docker-compose.yml`.

### Option 2: Local Development

1. Install dependencies:
```cmd
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
```

2. Run the app:
```cmd
python src\\app.py
```

The database is auto-created with 3 seed hospitals on first run.

3. Run tests:
```cmd
pytest -v
```

## Google Maps (optional)
**With Docker:** Add to `.env` file:
```
GOOGLE_MAPS_API_KEY=your_key_here
```

**Local development:**
```cmd
set GOOGLE_MAPS_API_KEY=your_key_here
```

Or pass in request body: `"google_api_key": "your_key"`
