"""Read-only product-owner acceptance tests. Models may not replace or edit this suite."""

import pytest
from fastapi.testclient import TestClient

from booking.api import create_app


@pytest.fixture
def client():
    return TestClient(create_app(":memory:"))


def room(client, name="A"):
    response = client.post("/rooms", json={"name": name, "capacity": 4})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def booking(room_id, start="10:00:00", end="11:00:00", title="Meeting"):
    return {"room_id": room_id, "title": title,
            "start": "2026-01-01T" + start, "end": "2026-01-01T" + end}


def test_r7_health(client):
    assert client.get("/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_r1_rooms(client):
    identifier = room(client)
    assert client.get("/rooms").json() == [{"id": identifier, "name": "A", "capacity": 4}]
    assert client.post("/rooms", json={"name": "A", "capacity": 4}).status_code == 409
    assert client.post("/rooms", json={"name": "B", "capacity": 0}).status_code == 422


def test_r2_r5_book_and_list_sorted(client):
    identifier = room(client)
    for start, end in [("12:00:00", "13:00:00"), ("09:00:00", "10:00:00")]:
        result = client.post("/bookings", json=booking(identifier, start, end))
        assert result.status_code == 201, result.text
        assert set(result.json()) == {"id", "room_id", "title", "start", "end"}
    rows = client.get(f"/rooms/{identifier}/bookings").json()
    assert len(rows) == 2
    assert [row["start"] for row in rows] == sorted(row["start"] for row in rows)


@pytest.mark.parametrize("start,end", [("09:30:00", "10:30:00"), ("10:30:00", "11:30:00"),
                                       ("09:00:00", "12:00:00"), ("10:15:00", "10:45:00")])
def test_r3_overlap(client, start, end):
    identifier = room(client)
    assert client.post("/bookings", json=booking(identifier)).status_code == 201
    assert client.post("/bookings", json=booking(identifier, start, end)).status_code == 409


def test_r3_back_to_back_and_other_room(client):
    identifier = room(client)
    assert client.post("/bookings", json=booking(identifier)).status_code == 201
    assert client.post("/bookings", json=booking(identifier, "11:00:00", "12:00:00")).status_code == 201
    assert client.post("/bookings", json=booking(identifier, "09:00:00", "10:00:00")).status_code == 201
    assert client.post("/bookings", json=booking(room(client, "B"))).status_code == 201


@pytest.mark.parametrize("start,end,title", [("11:00:00", "10:00:00", "Meeting"),
                                             ("10:00:00", "10:00:00", "Meeting"),
                                             ("10:00:00", "11:00:00", "")])
def test_r4_validation(client, start, end, title):
    identifier = room(client)
    assert client.post("/bookings", json=booking(identifier, start, end, title)).status_code == 422


def test_r4_unknown_room(client):
    assert client.post("/bookings", json=booking(999)).status_code == 404
    assert client.get("/rooms/999/bookings").status_code == 404


def test_r6_cancel(client):
    identifier = room(client)
    result = client.post("/bookings", json=booking(identifier))
    assert result.status_code == 201
    booked = result.json()["id"]
    response = client.delete(f"/bookings/{booked}")
    assert response.status_code == 204 and response.content == b""
    assert client.get(f"/rooms/{identifier}/bookings").json() == []
    assert client.delete(f"/bookings/{booked}").status_code == 404

