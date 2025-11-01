from __future__ import annotations

import math
import os
import uuid
import json
import signal
import sys
import threading
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from urllib.parse import urlparse
from os import environ
import time

import requests
import pika
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, Integer, create_engine, text

import amqp_setup

# Get database connection from environment and parse robustly
raw_db_conn = environ.get("db_conn")
db_url = None
if raw_db_conn:
    # If a full URL is provided use it. If not, assume mysql+mysqlconnector scheme
    if "://" in raw_db_conn:
        db_url = urlparse(raw_db_conn)
    else:
        # prepend scheme so urlparse fills hostname/port properly
        db_url = urlparse("mysql+mysqlconnector://" + raw_db_conn)

if db_url:
    print(f"DB connection string parsed: scheme={db_url.scheme}, hostname={db_url.hostname}, port={db_url.port}")
else:
    print("No db_conn environment variable found; defaulting to SQLite")

# Create a singleton instance
amqp = amqp_setup.AMQPSetup()

# Flag to control the consumer loop
should_stop = False

# Track active dispatches with vitals monitoring
active_dispatches = {}  # {dispatch_id: {"patient_id": ..., "stop_monitoring": False}}


def signal_handler(sig, frame):
    global should_stop
    print("Stopping consumer...")
    should_stop = True
    # Stop all vitals monitoring threads
    for dispatch_id in active_dispatches:
        active_dispatches[dispatch_id]["stop_monitoring"] = True


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
    except Exception as e:
        print(f"Failed to publish event to {routing_key}: {e}")
        return False


def generate_simulated_vitals():
    """Generate simulated patient vitals for monitoring.
    In a real system, this would come from actual medical sensors.
    """
    import random
    return {
        "heart_rate": random.randint(60, 140),
        "blood_pressure": f"{random.randint(110, 140)}/{random.randint(70, 90)}",
        "spo2": random.randint(90, 100),
        "temperature": round(random.uniform(36.5, 38.5), 1)
    }


def monitor_patient_vitals(dispatch_id: str, patient_id: str):
    """Continuously monitor and publish patient vitals every 5 seconds.
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
        print(f"[VITALS] Created dedicated RabbitMQ connection for {dispatch_id}")
    except Exception as e:
        print(f"[VITALS] Failed to create connection: {e}")
        return
    
    try:
        while not active_dispatches.get(dispatch_id, {}).get("stop_monitoring", False):
            try:
                # Generate vitals (in real system, this would come from sensors)
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
                print(f"[VITALS] Published vitals for {dispatch_id}: HR={vitals['heart_rate']}, BP={vitals['blood_pressure']}, SpO2={vitals['spo2']}%, Temp={vitals['temperature']}°C")
                
                # Wait 5 seconds before next update
                time.sleep(5)
                
            except Exception as e:
                print(f"[VITALS] Error monitoring vitals for {dispatch_id}: {e}")
                break
    finally:
        # Clean up dedicated connection
        if vitals_connection and vitals_connection.is_open:
            vitals_connection.close()
            print(f"[VITALS] Closed dedicated connection for {dispatch_id}")
    
    print(f"[VITALS] Stopped vitals monitoring for dispatch {dispatch_id}")


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


class Hospital(Base):
    __tablename__ = "hospitals"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=5)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "lat": self.lat,
            "lng": self.lng,
            "capacity": self.capacity,
        }


def haversine_distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Calculate great-circle distance (km) between two (lat, lng) points."""
    lat1, lon1 = a
    lat2, lon2 = b
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(x), math.sqrt(1 - x))


def find_hospitals_via_google(patient_loc: Tuple[float, float], radius_meters: int = 5000) -> List[Dict]:
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
        
    except Exception as e:
        print(f"Error calling Google Places API: {e}")
        return []


def pick_best_hospital(patient_loc: Tuple[float, float], severity: int = 1) -> Dict:
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
        for h in hospitals:
            h_dict = h.to_dict()
            dist = haversine_distance(patient_loc, (h.lat, h.lng))
            # capacity penalty: fewer free beds -> higher penalty
            capacity_penalty = max(0, 5 - h.capacity) * 0.5
            # severity increases weight of nearby hospitals
            score = dist + capacity_penalty - (severity * 0.1)
            candidates.append({**h_dict, "distance_km": round(dist, 3), "score": round(score, 3), "source": "database"})
        
        candidates.sort(key=lambda x: x["score"])
        print(f"Selected hospital from database: {candidates[0]['name']}")
        return candidates[0]
    
    else:
        # Fallback to Google Places API
        print("No hospitals in database, searching via Google Places API...")
        google_hospitals = find_hospitals_via_google(patient_loc)
        
        if not google_hospitals:
            raise ValueError("No hospitals available in database or via Google Places API")
        
        # Return the nearest hospital from Google
        best = google_hospitals[0]
        print(f"Selected hospital from Google Places: {best['name']}")
        return best


def estimate_eta_minutes(distance_km: float, speed_kmph: float = 50.0) -> int:
    # simple ETA: time = distance / speed. Round up to nearest minute.
    hours = distance_km / speed_kmph if speed_kmph > 0 else 0
    minutes = int(math.ceil(hours * 60))
    return max(1, minutes)


def create_app(db_uri: Optional[str] = None) -> Flask:
    app = Flask(__name__)
    
    # Use MySQL connection from environment or fallback to SQLite for local testing
    if db_uri is None:
        if db_url:
            # Build MySQL connection string
            db_uri = f"mysql+mysqlconnector://{db_url.username}:{db_url.password}@{db_url.hostname}:{db_url.port or 3306}/dispatch"
        else:
            db_uri = "sqlite:///hospitals.db"
    
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    
    CORS(app)
    db.init_app(app)
    
    with app.app_context():
        # Try to connect to DB and create tables with a retry loop so the service
        # waits for MySQL to become available (useful in containers/startup).
        max_retry_time = int(environ.get("DB_MAX_RETRY", "60"))
        start_time = time.time()
        
        # If using MySQL, ensure the database exists before trying to create tables
        if db_uri and "mysql" in db_uri.lower():
            database_name = "dispatch"
            # Build connection string without database name to create it
            if db_url:
                base_uri = f"mysql+mysqlconnector://{db_url.username}:{db_url.password}@{db_url.hostname}:{db_url.port or 3306}"
            else:
                base_uri = db_uri.rsplit('/', 1)[0]  # Remove database name from URI
            
            while True:
                try:
                    # Connect without database and create it if it doesn't exist
                    engine = create_engine(base_uri)
                    with engine.connect() as conn:
                        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {database_name}"))
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
        count = db.session.execute(db.select(db.func.count()).select_from(Hospital)).scalar()
        if count == 0:
            seed_hospitals = [
                Hospital(id="hosp-1", name="Central Hospital", lat=51.5074, lng=-0.1278, capacity=5),
                Hospital(id="hosp-2", name="Westside Medical", lat=51.5155, lng=-0.1420, capacity=2),
                Hospital(id="hosp-3", name="Riverside Clinic", lat=51.5033, lng=-0.1196, capacity=10),
            ]
            db.session.add_all(seed_hospitals)
            db.session.commit()


    @app.get("/")
    def health():
        """Health check endpoint for Docker healthcheck."""
        return jsonify({"status": "ok", "service": "dispatch-amqp"})


    return app


def callback(ch, method, properties, body):
    """Process incoming dispatch command messages."""
    try:
        message_body = json.loads(body)
        print(f"[RECEIVED] Message from {method.routing_key}: {message_body}")
        
        # Handle different types of dispatch commands
        command = message_body.get("command")
        print(f"[DEBUG] Command type: {command}")
        
        if command == "dispatch_ambulance":
            # Process ambulance dispatch request
            patient_loc = message_body.get("patient_location")
            patient_id = message_body.get("patient_id", f"patient-{uuid.uuid4().hex[:8]}")
            hospital_id = message_body.get("hospital_id")
            
            if patient_loc and "lat" in patient_loc and "lng" in patient_loc:
                # Get app context
                app = create_app()
                with app.app_context():
                    if not hospital_id:
                        # Pick best hospital if none provided
                        best = pick_best_hospital((float(patient_loc["lat"]), float(patient_loc["lng"])))
                        hospital_id = best["id"]
                    
                    hospital = db.session.get(Hospital, hospital_id)
                    if hospital:
                        patient_point = (float(patient_loc["lat"]), float(patient_loc["lng"]))
                        hospital_point = (hospital.lat, hospital.lng)
                        
                        dist = haversine_distance(patient_point, hospital_point)
                        eta_min = estimate_eta_minutes(dist)
                        
                        dispatch_id = str(uuid.uuid4())
                        ambulance_id = f"amb-{dispatch_id[:8]}"
                        
                        # Publish hospital found event
                        publish_event("dispatch.updates.hospital_found", {
                            "dispatch_id": dispatch_id,
                            "patient_id": patient_id,
                            "hospital_id": hospital_id,
                            "hospital_name": hospital.name,
                            "hospital_location": {"lat": hospital.lat, "lng": hospital.lng},
                            "distance_km": round(dist, 3)
                        })
                        
                        # Publish ambulance sent event
                        publish_event("dispatch.updates.ambulance_sent", {
                            "dispatch_id": dispatch_id,
                            "patient_id": patient_id,
                            "ambulance_id": ambulance_id,
                            "hospital_id": hospital_id,
                            "eta_minutes": eta_min,
                            "route": {"from": patient_loc, "to": {"lat": hospital.lat, "lng": hospital.lng}}
                        })
                        
                        print(f"SUCCESS: Dispatched ambulance {ambulance_id} to {hospital.name}")
        
        elif command == "patient_onboard":
            # Patient has been picked up by ambulance
            dispatch_id = message_body.get("dispatch_id")
            patient_id = message_body.get("patient_id")
            ambulance_id = message_body.get("ambulance_id")
            
            publish_event("dispatch.updates.patient_onboard", {
                "dispatch_id": dispatch_id,
                "patient_id": patient_id,
                "ambulance_id": ambulance_id,
                "status": "onboard",
                "onboard_time": datetime.utcnow().isoformat()
            })
            print(f"Patient {patient_id} is now onboard ambulance {ambulance_id}")
            
            # Start continuous vitals monitoring in background thread
            active_dispatches[dispatch_id] = {
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
            print(f"[VITALS] Started continuous monitoring for {dispatch_id}")
        
        elif command == "update_vitals":
            # OPTIONAL: Manual vitals update (for external sensors or manual override)
            # NOTE: Automatic vitals are already published every 5 seconds after patient_onboard
            # This command is useful for:
            # - Real medical sensor data integration
            # - Manual updates from paramedics/nurses
            # - Override simulated vitals with actual readings
            dispatch_id = message_body.get("dispatch_id")
            patient_id = message_body.get("patient_id")
            vitals = message_body.get("vitals", {})
            
            publish_event("dispatch.updates.patient_vitals", {
                "dispatch_id": dispatch_id,
                "patient_id": patient_id,
                "vitals": vitals,
                "recorded_at": datetime.utcnow().isoformat(),
                "source": "manual"  # Distinguish from automatic vitals
            })
            print(f"[MANUAL] Updated vitals for patient {patient_id}: {vitals}")
        
        elif command == "reached_hospital":
            # Ambulance has arrived at hospital
            dispatch_id = message_body.get("dispatch_id")
            patient_id = message_body.get("patient_id")
            ambulance_id = message_body.get("ambulance_id")
            hospital_id = message_body.get("hospital_id")
            
            print(f"[DEBUG] reached_hospital - dispatch_id: {dispatch_id}")
            print(f"[DEBUG] active_dispatches keys: {list(active_dispatches.keys())}")
            print(f"[DEBUG] Is dispatch_id in active_dispatches? {dispatch_id in active_dispatches}")
            
            # Stop vitals monitoring for this dispatch
            if dispatch_id in active_dispatches:
                active_dispatches[dispatch_id]["stop_monitoring"] = True
                print(f"[VITALS] Stopping monitoring for {dispatch_id}")
            else:
                print(f"[VITALS] WARNING: dispatch_id '{dispatch_id}' not found in active_dispatches!")
            
            publish_event("dispatch.updates.reached_hospital", {
                "dispatch_id": dispatch_id,
                "patient_id": patient_id,
                "ambulance_id": ambulance_id,
                "hospital_id": hospital_id,
                "status": "arrived",
                "arrival_time": datetime.utcnow().isoformat()
            })
            print(f"Ambulance {ambulance_id} reached hospital {hospital_id}")
        
        else:
            print(f"Unknown command: {command}")
            
    except Exception as e:
        print(f"FAIL: Error processing message: {str(e)}")


def consume():
    """Start the RabbitMQ consumer."""
    try:
        # Ensure connection is established
        amqp.connect()

        # Set up consumer
        amqp.channel.basic_consume(
            queue=amqp.queue_name, on_message_callback=callback, auto_ack=True
        )

        print(" [*] Waiting for dispatch messages. To exit press CTRL+C")

        # Start consuming
        while not should_stop:
            try:
                amqp.connection.process_data_events(time_limit=1)
            except pika.exceptions.AMQPConnectionError:
                print("Connection lost, attempting to reconnect...")
                amqp.connect()

    except KeyboardInterrupt:
        print("Interrupted")
    except Exception as e:
        print(f"Error in consumer: {e}")
    finally:
        if hasattr(amqp, "connection") and amqp.connection and amqp.connection.is_open:
            amqp.close()
        sys.exit(0)


if __name__ == "__main__":
    # Start consumer in a separate thread (this will create the exchange/queue/bindings)
    consumer_thread = threading.Thread(target=consume, daemon=True)
    consumer_thread.start()

    # Optional: start the Flask HTTP server. Tests that only exercise AMQP
    # (publisher_test.py / consumer_test.py) may not want the Flask app to start
    # because DB initialization can fail if MySQL isn't available. Control this
    # via the START_FLASK environment variable (default: "true").
    start_flask = str(os.environ.get("START_FLASK", "true")).lower()
    if start_flask in ("1", "true", "yes", "on"):
        app = create_app()
        app.run("0.0.0.0", port=8080, debug=False, use_reloader=False)
    else:
        print("START_FLASK is falsey; skipping Flask startup and running only AMQP consumer.")
        try:
            # Keep the main thread alive while the consumer runs
            while consumer_thread.is_alive():
                consumer_thread.join(timeout=1)
        except KeyboardInterrupt:
            print("Shutting down consumer...")
