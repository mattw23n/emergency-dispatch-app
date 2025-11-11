"""Dispatch service for ambulance coordination and hospital selection."""
from __future__ import annotations

import json
import math
import os
import signal
import sys
import threading
import time
import uuid
from datetime import datetime
from os import environ
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import pika
import requests
from flask import Flask, jsonify
# from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Float, Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

try:
    from . import amqp_setup
except ImportError:
    import amqp_setup

# Get database connection from environment
# Support both legacy db_conn and individual AWS RDS configuration
RAW_DB_CONN = environ.get("db_conn")
DB_HOST = environ.get("DB_HOST")
DB_PORT = environ.get("DB_PORT", "3306")
DB_USER = environ.get("DB_USER")
DB_PASSWORD = environ.get("DB_PASSWORD")
DB_NAME = environ.get("DB_NAME")

DB_URL = None

# Priority 1: Use individual DB_* environment variables (AWS RDS)
if DB_HOST and DB_USER and DB_PASSWORD and DB_NAME:
    # Build connection string from individual components
    db_connection_string = (
        f"{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    DB_URL = urlparse("mysql+mysqlconnector://" + db_connection_string)
    print(f"Using AWS RDS: {DB_HOST}:{DB_PORT}/{DB_NAME}")

# Priority 2: Fall back to legacy db_conn if provided
elif RAW_DB_CONN:
    if "://" in RAW_DB_CONN:
        DB_URL = urlparse(RAW_DB_CONN)
    else:
        DB_URL = urlparse("mysql+mysqlconnector://" + RAW_DB_CONN)
    print(f"Using db_conn: {DB_URL.hostname}:{DB_URL.port}")

# Priority 3: Default to SQLite for local development
else:
    print("No database configuration found; defaulting to SQLite")

if DB_URL:
    db_path = DB_URL.path.lstrip('/')
    print(
        f"DB connection: scheme={DB_URL.scheme}, "
        f"hostname={DB_URL.hostname}, port={DB_URL.port}, "
        f"database={db_path}"
    )

# Create a singleton instance
amqp = amqp_setup.AMQPSetup()

# Flag to control the consumer loop
SHOULD_STOP = False

# Track active dispatches with vitals monitoring
ACTIVE_DISPATCHES = {}


def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    # pylint: disable=unused-argument
    global SHOULD_STOP  # pylint: disable=global-statement
    print("Stopping consumer...")
    SHOULD_STOP = True
    # Stop all vitals monitoring threads
    for dispatch_id, dispatch_data in ACTIVE_DISPATCHES.items():
        dispatch_data["stop_monitoring"] = True


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def publish_event(routing_key: str, message: dict):
    """Publish an event to the AMQP exchange."""
    try:
        message['timestamp'] = datetime.utcnow().isoformat()
        message_body = json.dumps(message)

        success = amqp.publish_event(message_body, routing_key)
        if success:
            print(f"Published event to {routing_key}: {message}")
        return success
    except Exception as publish_error:
        print(f"Failed to publish event to {routing_key}: {publish_error}")
        return False


def generate_simulated_vitals():
    """Generate simulated patient vitals for monitoring.
    In a real system, this would come from actual medical sensors.
    """
    import random  # pylint: disable=import-outside-toplevel
    return {
        "heart_rate": random.randint(60, 140),
        "blood_pressure": (
            f"{random.randint(110, 140)}/{random.randint(70, 90)}"
        ),
        "spo2": random.randint(90, 100),
        "temperature": round(random.uniform(36.5, 38.5), 1)
    }


def monitor_patient_vitals(dispatch_id: str, patient_id: str):
    """Continuously monitor and publish patient vitals every 2 seconds.
    Runs in a separate thread until the ambulance reaches the hospital.
    Uses its own RabbitMQ connection to avoid thread-safety issues.
    """
    print(f"[VITALS] Starting vitals monitoring for dispatch {dispatch_id}")

    # Create dedicated connection for this thread (pika is not thread-safe)
    vitals_connection = None
    try:
        rabbit_host = environ.get("RABBITMQ_HOST") or "localhost"
        rabbit_port = int(environ.get("RABBITMQ_PORT") or 5672)

        vitals_connection = pika.BlockingConnection(
            pika.ConnectionParameters(host=rabbit_host, port=rabbit_port)
        )
        vitals_channel = vitals_connection.channel()
        vitals_channel.exchange_declare(
            exchange='amqp.topic',
            exchange_type='topic',
            durable=True
        )
        print(
            f"[VITALS] Created dedicated RabbitMQ "
            f"connection for {dispatch_id}"
        )
    except Exception as conn_error:
        print(f"[VITALS] Failed to create connection: {conn_error}")
        return

    try:
        stop_flag = ACTIVE_DISPATCHES.get(dispatch_id, {})
        while not stop_flag.get("stop_monitoring", False):
            try:
                # Generate vitals (in real system, this would come from
                # sensors)
                vitals = generate_simulated_vitals()

                # Publish vitals update using dedicated channel
                event_data = {
                    "dispatch_id": dispatch_id,
                    "patient_id": patient_id,
                    "vitals": vitals,
                    "recorded_at": datetime.utcnow().isoformat(),
                    "timestamp": datetime.utcnow().isoformat()
                }

                vitals_channel.basic_publish(
                    exchange='amqp.topic',
                    routing_key='dispatch.updates.patient_vitals',
                    body=json.dumps(event_data)
                )
                # To actually read the vitals data,
                # need a consumer service listening to the
                # dispatch.updates.patient_vitals routing key.
                hr = vitals['heart_rate']
                bp = vitals['blood_pressure']
                spo2 = vitals['spo2']
                temp = vitals['temperature']
                print(
                    f"[VITALS] Published vitals for {dispatch_id}: "
                    f"HR={hr}, BP={bp}, SpO2={spo2}%, Temp={temp}°C"
                )

                # Wait 2 seconds before next update
                time.sleep(2)

            except Exception as vitals_error:
                print(
                    f"[VITALS] Error monitoring vitals for "
                    f"{dispatch_id}: {vitals_error}"
                )
                break
    finally:
        # Clean up dedicated connection
        if vitals_connection and vitals_connection.is_open:
            vitals_connection.close()
            print(f"[VITALS] Closed dedicated connection for {dispatch_id}")

    print(f"[VITALS] Stopped vitals monitoring for dispatch {dispatch_id}")


def automated_ambulance_workflow(
        dispatch_id: str,
        patient_id: str,
        ambulance_id: str,
        hospital_id: str):
    """Automated workflow that simulates the ambulance journey.

    Timeline:
    - Wait 5 seconds, then publish patient_onboard
    - Monitor vitals every 2 seconds for 10 seconds (5 vitals updates)
    - Publish reached_hospital
    """
    print(f"[DEBUG] Starting automated workflow for dispatch {dispatch_id}")

    # Create dedicated connection for this thread
    workflow_connection = None
    try:
        rabbit_host = environ.get("RABBITMQ_HOST") or "localhost"
        rabbit_port = int(environ.get("RABBITMQ_PORT") or 5672)

        workflow_connection = pika.BlockingConnection(
            pika.ConnectionParameters(host=rabbit_host, port=rabbit_port)
        )
        workflow_channel = workflow_connection.channel()
        workflow_channel.exchange_declare(
            exchange='amqp.topic',
            exchange_type='topic',
            durable=True
        )
        print(
            f"[DEBUG] Created dedicated RabbitMQ connection for {dispatch_id}")
    except Exception as e:
        print(f"[DEBUG] Failed to create connection: {e}")
        return

    try:
        # Step 1: Wait 5 seconds, then publish patient_onboard
        print("[DEBUG] Waiting 5 seconds before patient onboard...")
        time.sleep(5)

        onboard_event = {
            "incident_id": dispatch_id,
            "dispatch_id": dispatch_id,
            "patient_id": patient_id,
            "unit_id": ambulance_id,
            "ambulance_id": ambulance_id,
            "status": "onboard",
            "onboard_time": datetime.utcnow().isoformat(),
            "ts": datetime.utcnow().isoformat()
        }
        workflow_channel.basic_publish(
            exchange='amqp.topic',
            routing_key='event.dispatch.patient_onboard',
            body=json.dumps(onboard_event)
        )
        print(f"[EVENT] Published patient_onboard for {dispatch_id}")

        # Start vitals monitoring in the background
        ACTIVE_DISPATCHES[dispatch_id] = {
            "patient_id": patient_id,
            "stop_monitoring": False
        }
        vitals_thread = threading.Thread(
            target=monitor_patient_vitals,
            args=(dispatch_id, patient_id),
            daemon=True,
            name=f"vitals-{dispatch_id}"
        )
        vitals_thread.start()
        print(f"[EVENT] Started vitals monitoring for {dispatch_id}")

        # Step 2: Wait 10 seconds while vitals are being monitored
        print(
            "[EVENT] Waiting 10 seconds while vitals are monitored..."
        )
        time.sleep(10)

        # Step 3: Stop vitals monitoring and publish reached_hospital
        if dispatch_id in ACTIVE_DISPATCHES:
            ACTIVE_DISPATCHES[dispatch_id]["stop_monitoring"] = True
            print(f"[EVENT] Stopping vitals monitoring for {dispatch_id}")

        arrived_event = {
            "incident_id": dispatch_id,
            "dispatch_id": dispatch_id,
            "patient_id": patient_id,
            "unit_id": ambulance_id,
            "ambulance_id": ambulance_id,
            "hospital_id": hospital_id,
            "dest_hospital_id": hospital_id,
            "status": "arrived",
            "arrival_time": datetime.utcnow().isoformat(),
            "ts": datetime.utcnow().isoformat()
        }
        workflow_channel.basic_publish(
            exchange='amqp.topic',
            routing_key='event.dispatch.arrived_at_hospital',
            body=json.dumps(arrived_event)
        )
        print(f"[EVENT] Published reached_hospital for {dispatch_id}")
        print(f"[DEBUG] Workflow complete for dispatch {dispatch_id}")

    except Exception as e:
        print(f"[DEBUG] Error in workflow for {dispatch_id}: {e}")
    finally:
        if workflow_connection and workflow_connection.is_open:
            workflow_connection.close()
            print(f"[DEBUG] Closed dedicated connection for {dispatch_id}")


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


db = SQLAlchemy(model_class=Base)


class Hospital(Base):
    """Hospital model for storing hospital information."""

    __tablename__ = "hospitals"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=5)

    def to_dict(self) -> dict:
        """Convert hospital model to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "lat": self.lat,
            "lng": self.lng,
            "capacity": self.capacity,
        }


def haversine_distance(
        point_a: Tuple[float, float],
        point_b: Tuple[float, float]) -> float:
    """Calculate great-circle distance (km) between two (lat, lng) points."""
    lat1, lon1 = point_a
    lat2, lon2 = point_b
    earth_radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    haversine_a = (
        math.sin(dphi / 2) ** 2 +
        math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * earth_radius * math.atan2(
        math.sqrt(haversine_a), math.sqrt(1 - haversine_a)
    )


def find_hospitals_via_google(
        patient_loc: Tuple[float, float],
        radius_meters: int = 5000) -> List[Dict]:
    """Find hospitals near patient location using Google Places API.
    Returns list of hospitals sorted by distance.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        print("No Google Maps API key found, cannot search for hospitals")
        return []

    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{patient_loc[0]},{patient_loc[1]}",
        "radius": radius_meters,
        "type": "hospital",
        "key": api_key,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "OK":
            print(f"Google Places API returned status: {data.get('status')}")
            return []

        hospitals = []
        for place in data.get("results", [])[:5]:  # Limit to top 5
            loc = place.get("geometry", {}).get("location", {})
            hospital_loc = (loc.get("lat"), loc.get("lng"))
            dist = haversine_distance(patient_loc, hospital_loc)

            hospitals.append({
                "id": place.get("place_id"),
                "name": place.get("name"),
                "lat": hospital_loc[0],
                "lng": hospital_loc[1],
                "distance_km": round(dist, 3),
                "address": place.get("vicinity"),
                "source": "google_places"
            })

        hospitals.sort(key=lambda x: x["distance_km"])
        print(f"Found {len(hospitals)} hospitals via Google Places API")
        return hospitals

    except Exception as api_error:
        print(f"Error calling Google Places API: {api_error}")
        return []


def pick_best_hospital(
        patient_loc: Tuple[float, float], severity: int = 1) -> Dict:
    """Select the best hospital for a patient (hybrid approach).

    1. First tries to find hospitals in local database
    2. Falls back to Google Places API if database is empty

    Returns the hospital dict augmented with distance_km and score.
    """
    # Try database first
    hospitals = db.session.execute(db.select(Hospital)).scalars().all()

    if hospitals:
        # Use database hospitals with capacity scoring
        candidates = []
        for hospital in hospitals:
            hospital_dict = hospital.to_dict()
            dist = haversine_distance(
                patient_loc, (hospital.lat, hospital.lng))
            # capacity penalty: fewer free beds -> higher penalty
            capacity_penalty = max(0, 5 - hospital.capacity) * 0.5
            # severity increases weight of nearby hospitals
            score = dist + capacity_penalty - (severity * 0.1)
            candidates.append({
                **hospital_dict,
                "distance_km": round(dist, 3),
                "score": round(score, 3),
                "source": "database"
            })

        candidates.sort(key=lambda x: x["score"])
        print(f"Selected hospital from database: {candidates[0]['name']}")
        return candidates[0]

    # Fallback to Google Places API
    print("No hospitals in database, searching via Google Places API...")
    google_hospitals = find_hospitals_via_google(patient_loc)

    if not google_hospitals:
        raise ValueError(
            "No hospitals available in database or via Google Places API")

    # Return the nearest hospital from Google
    best = google_hospitals[0]
    print(f"Selected hospital from Google Places: {best['name']}")
    return best


def estimate_eta_minutes(distance_km: float, speed_kmph: float = 50.0) -> int:
    """Estimate ETA in minutes based on distance and speed."""
    hours = distance_km / speed_kmph if speed_kmph > 0 else 0
    minutes = int(math.ceil(hours * 60))
    return max(1, minutes)


def create_app(db_uri: Optional[str] = None) -> Flask:
    """Create and configure the Flask application."""
    flask_app = Flask(__name__)

    # Use MySQL connection from environment or fallback to SQLite
    if db_uri is None:
        if DB_URL:
            # Extract database name from path (e.g., /cs302DB or /dispatch)
            database_name = (
                DB_URL.path.lstrip('/') if DB_URL.path else "dispatch"
            )

            # Build MySQL connection string with the correct database
            db_uri = (
                f"mysql+mysqlconnector://{DB_URL.username}:"
                f"{DB_URL.password}@{DB_URL.hostname}:"
                f"{DB_URL.port or 3306}/{database_name}"
            )
        else:
            db_uri = "sqlite:///hospitals.db"

    flask_app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    flask_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # CORS(flask_app)
    db.init_app(flask_app)

    with flask_app.app_context():
        # Try to connect to DB and create tables with a retry loop
        # waits for MySQL to become available (containers/startup).
        max_retry_time = int(environ.get("DB_MAX_RETRY", "60"))
        start_time = time.time()

        # If using MySQL, ensure the database exists before creating tables
        if db_uri and "mysql" in db_uri.lower():
            # Extract database name from URI
            if DB_URL and DB_URL.path:
                database_name = DB_URL.path.lstrip('/')
            else:
                database_name = db_uri.rsplit('/', 1)[-1]

            # Validate database name to prevent SQL injection
            import re
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', database_name):
                raise ValueError(f"Invalid database name: {database_name}")

            # Build connection string without database name to create it
            if DB_URL:
                base_uri = (
                    f"mysql+mysqlconnector://{DB_URL.username}:"
                    f"{DB_URL.password}@{DB_URL.hostname}:"
                    f"{DB_URL.port or 3306}"
                )
            else:
                # Remove database name from URI
                base_uri = db_uri.rsplit('/', 1)[0]

            while True:
                try:
                    # Connect without database and create it if it doesn't
                    # exist
                    engine = create_engine(base_uri)
                    with engine.connect() as conn:
                        create_db_sql = (
                            f"CREATE DATABASE IF NOT EXISTS {database_name}"
                        )
                        # Database name is validated with regex above, safe from SQL injection
                        # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
                        conn.execute(text(create_db_sql))
                        conn.commit()
                    engine.dispose()
                    print(f"Database '{database_name}' ensured to exist")
                    break
                except Exception as e:
                    print(f"Database creation check failed: {e}")
                    if time.time() - start_time > max_retry_time:
                        print("Max DB retry time exceeded; aborting startup.")
                        raise
                    print("Retrying database creation in 2 seconds...")
                    time.sleep(2)

        # Now try to create tables
        while True:
            try:
                db.create_all()
                print("Database tables created successfully")
                break
            except Exception as e:
                print(f"DB connection/create_all failed: {e}")
                if time.time() - start_time > max_retry_time:
                    print("Max DB retry time exceeded; aborting startup.")
                    raise
                print("Retrying DB connection in 2 seconds...")
                time.sleep(2)

        # Seed with example data if database is empty
        count = db.session.execute(
            db.select(db.func.count()).select_from(Hospital)).scalar()
        if count == 0:
            seed_hospitals = [
                Hospital(
                    id="hosp-1",
                    name="Singapore General Hospital",
                    lat=1.2789,
                    lng=103.8358,
                    capacity=10),
                Hospital(
                    id="hosp-2",
                    name="Raffles Hospital",
                    lat=1.2998,
                    lng=103.8484,
                    capacity=8),
                Hospital(
                    id="hosp-3",
                    name="Mount Elizabeth Hospital",
                    lat=1.3054,
                    lng=103.8354,
                    capacity=12),
            ]
            db.session.add_all(seed_hospitals)
            db.session.commit()

    @flask_app.get("/health")
    def health():
        """Health check endpoint for Docker healthcheck."""
        return jsonify({"status": "ok", "service": "dispatch-amqp"})

    return flask_app


def callback(channel, method, properties, body):
    """Process incoming dispatch command messages.
    Only accepts 'request_ambulance' command.
    """
    # pylint: disable=unused-argument
    try:
        message_body = json.loads(body)
        print(f"[RECEIVED] Message from {method.routing_key}: {message_body}")

        # Only handle request_ambulance command
        command = message_body.get("command")
        print(f"[DEBUG] Command type: {command}")

        if command == "request_ambulance":
            # Process ambulance dispatch request
            # Accept both "location" and "patient_location"
            patient_loc = message_body.get("location")
            patient_id = message_body.get(
                "patient_id", f"patient-{uuid.uuid4().hex[:8]}")
            hospital_id = message_body.get("hospital_id")

            if patient_loc and "lat" in patient_loc and "lng" in patient_loc:
                # Get app context
                dispatch_app = create_app()
                with dispatch_app.app_context():
                    if not hospital_id:
                        # Pick best hospital if none provided
                        best = pick_best_hospital(
                            (float(
                                patient_loc["lat"]), float(
                                patient_loc["lng"])))
                        hospital_id = best["id"]

                    hospital = db.session.get(Hospital, hospital_id)
                    if hospital:
                        patient_point = (
                            float(
                                patient_loc["lat"]), float(
                                patient_loc["lng"]))
                        hospital_point = (hospital.lat, hospital.lng)

                        dist = haversine_distance(
                            patient_point, hospital_point)
                        eta_min = estimate_eta_minutes(dist)

                        dispatch_id = str(uuid.uuid4())
                        ambulance_id = f"amb-{dispatch_id[:8]}"

                        # Publish ambulance assigned event
                        hospital_location = {
                            "lat": hospital.lat,
                            "lng": hospital.lng
                        }
                        publish_event("event.dispatch.unit_assigned", {
                            "incident_id": dispatch_id,
                            "dispatch_id": dispatch_id,
                            "patient_id": patient_id,
                            "unit_id": ambulance_id,
                            "hospital_id": hospital_id,
                            "hospital_name": hospital.name,
                            "dest_hospital_id": hospital_id,
                            "location": hospital_location,
                            "distance_km": round(dist, 3),
                            "eta_minutes": eta_min,
                            "status": "unit_assigned",
                            "ts": datetime.utcnow().isoformat()
                        })

                        # Publish ambulance enroute event
                        route_to = {
                            "lat": hospital.lat,
                            "lng": hospital.lng
                        }
                        publish_event("event.dispatch.enroute", {
                            "incident_id": dispatch_id,
                            "dispatch_id": dispatch_id,
                            "patient_id": patient_id,
                            "unit_id": ambulance_id,
                            "hospital_id": hospital_id,
                            "dest_hospital_id": hospital_id,
                            "location": hospital_location,
                            "eta_minutes": eta_min,
                            "status": "enroute",
                            "route": {"from": patient_loc, "to": route_to},
                            "ts": datetime.utcnow().isoformat()
                        })

                        print(
                            f"SUCCESS: Dispatched ambulance {ambulance_id} "
                            f"to {hospital.name}"
                        )

                        # Start automated workflow in background thread
                        # This handles: patient_onboard -> vitals
                        # monitoring -> reached_hospital
                        workflow_thread = threading.Thread(
                            target=automated_ambulance_workflow,
                            args=(
                                dispatch_id,
                                patient_id,
                                ambulance_id,
                                hospital_id),
                            daemon=True,
                            name=f"workflow-{dispatch_id}")
                        workflow_thread.start()
                        print(f"Started workflow thread for {dispatch_id}")
            else:
                print(
                    f"ERROR: Invalid patient_location in "
                    f"request_ambulance: {patient_loc}"
                )

        else:
            print(
                f"IGNORED: Command '{command}' - "
                "only 'request_ambulance' is supported"
            )

    except Exception as callback_error:
        print(f"FAIL: Error processing message: {str(callback_error)}")


def consume():
    """Start the RabbitMQ consumer."""
    global SHOULD_STOP  # noqa: F824
    try:
        # Ensure connection is established
        amqp.connect()

        # Set up consumer
        amqp.channel.basic_consume(
            queue=amqp.queue_name,
            on_message_callback=callback,
            auto_ack=True
        )

        print(" [*] Waiting for dispatch messages. To exit press CTRL+C")

        # Start consuming
        while not SHOULD_STOP:
            try:
                amqp.connection.process_data_events(time_limit=1)
            except pika.exceptions.AMQPConnectionError:
                print("Connection lost, attempting to reconnect...")
                amqp.connect()

    except KeyboardInterrupt:
        print("Interrupted")
    except Exception as consumer_error:
        print(f"Error in consumer: {consumer_error}")
    finally:
        if hasattr(amqp, "connection") and amqp.connection:
            if amqp.connection.is_open:
                amqp.close()
        sys.exit(0)


if __name__ == "__main__":
    # Start consumer in a separate thread (this will create the
    # exchange/queue/bindings)
    consumer_thread = threading.Thread(target=consume, daemon=True)
    consumer_thread.start()

    # Optional: start the Flask HTTP server. Tests that only exercise AMQP
    # may not want the Flask app to start because DB initialization can fail
    # if MySQL isn't available. Control via START_FLASK env (default: "true").
    START_FLASK = str(os.environ.get("START_FLASK", "true")).lower()
    if START_FLASK in ("1", "true", "yes", "on"):
        main_app = create_app()
        # nosemgrep: python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host
        main_app.run("0.0.0.0", port=8081, debug=False, use_reloader=False)
    else:
        print(
            "START_FLASK is falsey; skipping Flask startup "
            "and running only AMQP consumer."
        )
        try:
            # Keep the main thread alive while the consumer runs
            while consumer_thread.is_alive():
                consumer_thread.join(timeout=1)
        except KeyboardInterrupt:
            print("Shutting down consumer...")
