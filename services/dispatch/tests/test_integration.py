def test_health(client):
	r = client.get("/")
	assert r.status_code == 200
	data = r.get_json()
	assert data["service"] == "hospital-dispatch-combined"


def test_best_hospital_requires_location(client):
	r = client.post("/hospital/best", json={})
	assert r.status_code == 400


def test_best_hospital_returns_candidate(client):
	payload = {"location": {"lat": 51.51, "lng": -0.12}, "severity": 2}
	r = client.post("/hospital/best", json=payload)
	assert r.status_code == 200
	j = r.get_json()
	assert "best_hospital" in j


def test_dispatch_ambulance_picks_and_returns_201(client):
    payload = {"patient_location": {"lat": 51.51, "lng": -0.12}}
    r = client.post("/dispatch/dispatch", json=payload)
    assert r.status_code == 201
    j = r.get_json()
    assert "dispatch_id" in j
    assert "ambulance" in j


def test_nearest_hospital_returns_closest_by_distance(client):
    payload = {"location": {"lat": 51.51, "lng": -0.12}}
    r = client.post("/hospital/nearest", json=payload)
    assert r.status_code == 200
    j = r.get_json()
    assert "nearest_hospital" in j
    assert "distance_km" in j["nearest_hospital"]


def test_list_hospitals(client):
    r = client.get("/hospital/list")
    assert r.status_code == 200
    j = r.get_json()
    assert "hospitals" in j
    assert len(j["hospitals"]) >= 3  # seeded data


def test_add_hospital(client):
    payload = {"id": "hosp-test", "name": "Test Hospital", "lat": 51.5, "lng": -0.1, "capacity": 8}
    r = client.post("/hospital/add", json=payload)
    assert r.status_code == 201
    j = r.get_json()
    assert j["hospital"]["id"] == "hosp-test"


def test_update_hospital(client):
    payload = {"capacity": 15}
    r = client.put("/hospital/hosp-1", json=payload)
    assert r.status_code == 200
    j = r.get_json()
    assert j["hospital"]["capacity"] == 15


def test_delete_hospital(client):
    r = client.delete("/hospital/hosp-2")
    assert r.status_code == 200
    j = r.get_json()
    assert j["id"] == "hosp-2"