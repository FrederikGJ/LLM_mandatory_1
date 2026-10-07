def test_bookings_sorted(client):
    room = client.post("/rooms", json=_room())
    assert room.status_code == 201
    identifier = room.json()["id"]
    for start, end in [("12:00:00", "13:00:00"), ("09:00:00", "10:00:00")]:
        assert client.post("/bookings", json=_booking(identifier, start, end)).status_code == 201
    response = client.get(f"/rooms/{identifier}/bookings")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    assert [row["start"] for row in rows] == sorted(row["start"] for row in rows)


def test_overlap_and_back_to_back(client):
    room = client.post("/rooms", json=_room())
    assert room.status_code == 201
    identifier = room.json()["id"]
    assert client.post("/bookings", json=_booking(identifier)).status_code == 201
    assert client.post(
        "/bookings", json=_booking(identifier, "10:30:00", "11:30:00")
    ).status_code == 409
    assert client.post(
        "/bookings", json=_booking(identifier, "11:00:00", "12:00:00")
    ).status_code == 201


def test_invalid_booking_and_missing_room(client):
    room = client.post("/rooms", json=_room())
    assert room.status_code == 201
    identifier = room.json()["id"]
    assert client.post(
        "/bookings", json=_booking(identifier, "11:00:00", "10:00:00")
    ).status_code == 422
    assert client.post("/bookings", json=_booking(identifier, title="")).status_code == 422
    assert client.post("/bookings", json=_booking(999)).status_code == 404


def test_cancel_booking(client):
    room = client.post("/rooms", json=_room())
    assert room.status_code == 201
    identifier = room.json()["id"]
    booked = client.post("/bookings", json=_booking(identifier))
    assert booked.status_code == 201
    booking_id = booked.json()["id"]
    response = client.delete(f"/bookings/{booking_id}")
    assert response.status_code == 204 and response.content == b""
    response = client.get(f"/rooms/{identifier}/bookings")
    assert response.status_code == 200 and response.json() == []
    assert client.delete(f"/bookings/{booking_id}").status_code == 404
