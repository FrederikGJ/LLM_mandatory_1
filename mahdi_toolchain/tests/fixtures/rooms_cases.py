def test_health_and_rooms(client):
    response = client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    created = client.post("/rooms", json=_room())
    assert created.status_code == 201
    listed = client.get("/rooms")
    assert listed.status_code == 200 and listed.json() == [created.json()]


def test_duplicate_room_name(client):
    assert client.post("/rooms", json=_room()).status_code == 201
    assert client.post("/rooms", json=_room()).status_code == 409


def test_room_capacity(client):
    assert client.post("/rooms", json=_room(capacity=0)).status_code == 422
    assert client.post("/rooms", json=_room(capacity=1)).status_code == 201


def test_unknown_room_bookings(client):
    assert client.get("/rooms/999/bookings").status_code == 404
