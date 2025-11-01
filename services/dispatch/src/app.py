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

import requests
import pika
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, Integer

import amqp_setup

# Get database connection from environment
db_url = urlparse(environ.get("db_conn"))

# Create a singleton instance
amqp = amqp_setup.AMQPSetup()

# Flag to control the consumer loop
should_stop = False


def signal_handler(sig, frame):
    global should_stop
    print("Stopping consumer...")
    should_stop = True


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


def pick_best_hospital(patient_loc: Tuple[float, float], severity: int = 1) -> Dict:
    """Select the best hospital for a patient based on a simple heuristic:
    shortest distance with a small capacity penalty and severity bias.
    Returns the hospital dict augmented with distance_km and score.
    """
    hospitals = db.session.execute(db.select(Hospital)).scalars().all()
    if not hospitals:
        raise ValueError("No hospitals available in database")
    
    candidates = []
    for h in hospitals:
        h_dict = h.to_dict()
        dist = haversine_distance(patient_loc, (h.lat, h.lng))
        # capacity penalty: fewer free beds -> higher penalty
        capacity_penalty = max(0, 5 - h.capacity) * 0.5
        # severity increases weight of nearby hospitals (i.e., reduce score if severity high and distance small)
        score = dist + capacity_penalty - (severity * 0.1)
        candidates.append({**h_dict, "distance_km": round(dist, 3), "score": round(score, 3)})

    candidates.sort(key=lambda x: x["score"])
    return candidates[0]


def estimate_eta_minutes(distance_km: float, speed_kmph: float = 50.0) -> int:
    # simple ETA: time = distance / speed. Round up to nearest minute.
    hours = distance_km / speed_kmph if speed_kmph > 0 else 0
    minutes = int(math.ceil(hours * 60))
    return max(1, minutes)


def find_nearest_hospital(patient_loc: Tuple[float, float]) -> Dict:
    """Find the nearest hospital by distance only (no capacity or severity weighting).
    Returns the hospital dict augmented with distance_km.
    """
    hospitals = db.session.execute(db.select(Hospital)).scalars().all()
    if not hospitals:
        raise ValueError("No hospitals available in database")
    
    candidates = []
    for h in hospitals:
        h_dict = h.to_dict()
        dist = haversine_distance(patient_loc, (h.lat, h.lng))
        candidates.append({**h_dict, "distance_km": round(dist, 3)})
    
    candidates.sort(key=lambda x: x["distance_km"])
    return candidates[0]


def get_google_directions(origin: Tuple[float, float], destination: Tuple[float, float], api_key: str, mode: str = "driving") -> Optional[Dict]:
    """Call Google Maps Directions API and return a simplified route dict.
    Returns None if API call fails or no route is found.
    """
    if not api_key:
        return None

    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "mode": mode,
        "key": api_key,
    }
    try:
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "OK" or not data.get("routes"):
            return None
        route = data["routes"][0]
        leg = route.get("legs", [])[0]
        distance_m = leg.get("distance", {}).get("value")
        duration_s = leg.get("duration", {}).get("value")
        overview_polyline = route.get("overview_polyline", {}).get("points")

        return {
            "distance_km": round(distance_m / 1000.0, 3) if distance_m is not None else None,
            "duration_seconds": duration_s,
            "duration_minutes": int(round(duration_s / 60)) if duration_s is not None else None,
            "polyline": overview_polyline,
            "raw": route,
        }
    except Exception:
        return None


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
        db.create_all()
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
        return jsonify({"status": "ok", "service": "hospital-dispatch-combined"})


    @app.post("/hospital/best")
    def best_hospital():
        payload = request.get_json(force=True) or {}
        loc = payload.get("location")
        if not loc or "lat" not in loc or "lng" not in loc:
            return jsonify({"error": "location required as {lat,lng}"}), 400
        severity = int(payload.get("severity", 1))
        patient_loc = (float(loc["lat"]), float(loc["lng"]))
        best = pick_best_hospital(patient_loc, severity)
        return jsonify({"best_hospital": best})


    @app.post("/hospital/nearest")
    def nearest_hospital():
        """Find the nearest hospital by distance only (real-time search)."""
        payload = request.get_json(force=True) or {}
        loc = payload.get("location")
        if not loc or "lat" not in loc or "lng" not in loc:
            return jsonify({"error": "location required as {lat,lng}"}), 400
        patient_loc = (float(loc["lat"]), float(loc["lng"]))
        nearest = find_nearest_hospital(patient_loc)
        return jsonify({"nearest_hospital": nearest})


    @app.get("/hospital/list")
    def list_hospitals():
        """List all hospitals in the database."""
        hospitals = db.session.execute(db.select(Hospital)).scalars().all()
        return jsonify({"hospitals": [h.to_dict() for h in hospitals]})


    @app.post("/hospital/add")
    def add_hospital():
        """Add a new hospital to the database."""
        payload = request.get_json(force=True) or {}
        required = ["id", "name", "lat", "lng"]
        if not all(k in payload for k in required):
            return jsonify({"error": f"required fields: {required}"}), 400
        
        if db.session.get(Hospital, payload["id"]):
            return jsonify({"error": "hospital with this id already exists"}), 409
        
        hospital = Hospital(
            id=payload["id"],
            name=payload["name"],
            lat=float(payload["lat"]),
            lng=float(payload["lng"]),
            capacity=int(payload.get("capacity", 5)),
        )
        db.session.add(hospital)
        db.session.commit()
        return jsonify({"hospital": hospital.to_dict()}), 201


    @app.put("/hospital/<hospital_id>")
    def update_hospital(hospital_id: str):
        """Update an existing hospital."""
        hospital = db.session.get(Hospital, hospital_id)
        if not hospital:
            return jsonify({"error": "hospital not found"}), 404
        
        payload = request.get_json(force=True) or {}
        if "name" in payload:
            hospital.name = payload["name"]
        if "lat" in payload:
            hospital.lat = float(payload["lat"])
        if "lng" in payload:
            hospital.lng = float(payload["lng"])
        if "capacity" in payload:
            hospital.capacity = int(payload["capacity"])
        
        db.session.commit()
        return jsonify({"hospital": hospital.to_dict()})


    @app.delete("/hospital/<hospital_id>")
    def delete_hospital(hospital_id: str):
        """Delete a hospital from the database."""
        hospital = db.session.get(Hospital, hospital_id)
        if not hospital:
            return jsonify({"error": "hospital not found"}), 404
        
        db.session.delete(hospital)
        db.session.commit()
        return jsonify({"message": "hospital deleted", "id": hospital_id})


    @app.post("/dispatch/dispatch")
    def dispatch_ambulance():
        payload = request.get_json(force=True) or {}
        patient_loc = payload.get("patient_location")
        patient_id = payload.get("patient_id", f"patient-{uuid.uuid4().hex[:8]}")
        hospital_id = payload.get("hospital_id")
        use_google = bool(payload.get("use_google", False))
        # allow request override of API key, otherwise fall back to env var
        google_key = payload.get("google_api_key") or os.environ.get("GOOGLE_MAPS_API_KEY")
        if not patient_loc or "lat" not in patient_loc or "lng" not in patient_loc:
            return jsonify({"error": "patient_location required as {lat,lng}"}), 400

        if not hospital_id:
            # pick best hospital if none provided
            best = pick_best_hospital((float(patient_loc["lat"]), float(patient_loc["lng"])))
            hospital_id = best["id"]
        hospital = db.session.get(Hospital, hospital_id)
        if not hospital:
            return jsonify({"error": "unknown hospital_id"}), 404

        patient_point = (float(patient_loc["lat"]), float(patient_loc["lng"]))
        hospital_point = (hospital.lat, hospital.lng)

        # If requested and API key available, try Google Directions for a realistic route and ETA
        google_route = None
        if use_google or google_key:
            google_route = get_google_directions(patient_point, hospital_point, google_key) if google_key else None

        if google_route:
            dist = google_route.get("distance_km") or haversine_distance(patient_point, hospital_point)
            eta_min = google_route.get("duration_minutes") or estimate_eta_minutes(dist)
            route_data = {"from": patient_loc, "to": {"lat": hospital.lat, "lng": hospital.lng}, "polyline": google_route.get("polyline")}
        else:
            dist = haversine_distance(patient_point, hospital_point)
            eta_min = estimate_eta_minutes(dist)
            route_data = {"from": patient_loc, "to": {"lat": hospital.lat, "lng": hospital.lng}}

        dispatch_id = str(uuid.uuid4())
        ambulance_id = f"amb-{dispatch_id[:8]}"
        ambulance = {
            "ambulance_id": ambulance_id,
            "eta_minutes": eta_min,
            "distance_km": round(dist, 3),
            "route": route_data,
        }

        # Publish dispatch events to message broker
        # Event 1: Hospital Found
        publish_event("dispatch.updates.hospital_found", {
            "dispatch_id": dispatch_id,
            "patient_id": patient_id,
            "hospital_id": hospital_id,
            "hospital_name": hospital.name,
            "hospital_location": {"lat": hospital.lat, "lng": hospital.lng},
            "distance_km": round(dist, 3)
        })

        # Event 2: Ambulance Sent
        publish_event("dispatch.updates.ambulance_sent", {
            "dispatch_id": dispatch_id,
            "patient_id": patient_id,
            "ambulance_id": ambulance_id,
            "hospital_id": hospital_id,
            "eta_minutes": eta_min,
            "route": route_data
        })

        result = {"dispatch_id": dispatch_id, "hospital": hospital.to_dict(), "ambulance": ambulance}
        return jsonify(result), 201


    @app.post("/dispatch/patient_onboard")
    def patient_onboard():
        """Record when patient is onboard the ambulance."""
        payload = request.get_json(force=True) or {}
        dispatch_id = payload.get("dispatch_id")
        patient_id = payload.get("patient_id")
        ambulance_id = payload.get("ambulance_id")
        
        if not dispatch_id or not patient_id or not ambulance_id:
            return jsonify({"error": "dispatch_id, patient_id, and ambulance_id required"}), 400
        
        # Publish patient onboard event
        publish_event("dispatch.updates.patient_onboard", {
            "dispatch_id": dispatch_id,
            "patient_id": patient_id,
            "ambulance_id": ambulance_id,
            "status": "onboard",
            "onboard_time": datetime.utcnow().isoformat()
        })
        
        return jsonify({
            "message": "Patient onboard event published",
            "dispatch_id": dispatch_id,
            "patient_id": patient_id
        }), 200


    @app.post("/dispatch/patient_vitals")
    def patient_vitals():
        """Record and publish patient vital signs during transport."""
        payload = request.get_json(force=True) or {}
        dispatch_id = payload.get("dispatch_id")
        patient_id = payload.get("patient_id")
        vitals = payload.get("vitals")
        
        if not dispatch_id or not patient_id or not vitals:
            return jsonify({"error": "dispatch_id, patient_id, and vitals required"}), 400
        
        # Publish patient vitals event
        publish_event("dispatch.updates.patient_vitals", {
            "dispatch_id": dispatch_id,
            "patient_id": patient_id,
            "vitals": vitals,
            "recorded_at": datetime.utcnow().isoformat()
        })
        
        return jsonify({
            "message": "Patient vitals event published",
            "dispatch_id": dispatch_id,
            "patient_id": patient_id
        }), 200


    @app.post("/dispatch/reached_hospital")
    def reached_hospital():
        """Record when ambulance has reached the hospital."""
        payload = request.get_json(force=True) or {}
        dispatch_id = payload.get("dispatch_id")
        patient_id = payload.get("patient_id")
        ambulance_id = payload.get("ambulance_id")
        hospital_id = payload.get("hospital_id")
        
        if not dispatch_id or not patient_id or not ambulance_id or not hospital_id:
            return jsonify({"error": "dispatch_id, patient_id, ambulance_id, and hospital_id required"}), 400
        
        # Publish reached hospital event
        publish_event("dispatch.updates.reached_hospital", {
            "dispatch_id": dispatch_id,
            "patient_id": patient_id,
            "ambulance_id": ambulance_id,
            "hospital_id": hospital_id,
            "status": "arrived",
            "arrival_time": datetime.utcnow().isoformat()
        })
        
        return jsonify({
            "message": "Reached hospital event published",
            "dispatch_id": dispatch_id,
            "hospital_id": hospital_id
        }), 200


    return app


def callback(ch, method, properties, body):
    """Process incoming dispatch command messages."""
    try:
        message_body = json.loads(body)
        print(f"[RECEIVED] Message from {method.routing_key}: {message_body}")
        
        # Handle different types of dispatch commands
        command = message_body.get("command")
        
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
    # Start consumer in a separate thread
    consumer_thread = threading.Thread(target=consume, daemon=True)
    consumer_thread.start()
    
    # Start Flask app
    app = create_app()
    app.run("0.0.0.0", port=8080, debug=False, use_reloader=False)
