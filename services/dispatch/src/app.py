from __future__ import annotations

import math
import os
import uuid
from typing import Dict, List, Tuple, Optional

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, Integer

import amqp_setup

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


def create_app(db_uri: str = "sqlite:///hospitals.db") -> Flask:
    app = Flask(__name__)
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
        ambulance = {
            "ambulance_id": f"amb-{dispatch_id[:8]}",
            "eta_minutes": eta_min,
            "distance_km": round(dist, 3),
            "route": route_data,
        }

        # In a real system this would publish a DispatchAmbulance event to a broker.
        result = {"dispatch_id": dispatch_id, "hospital": hospital.to_dict(), "ambulance": ambulance}
        return jsonify(result), 201


    return app


if __name__ == "__main__":
    app = create_app()
    app.run("0.0.0.0", port=8080, debug=True)
