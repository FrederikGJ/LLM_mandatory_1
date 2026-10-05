# SPEC: Meeting room booking

A small REST API where employees list meeting rooms and book them.
This file is the product owner's input. Agents must not change it.

## Functional requirements
- R1 Rooms: create a room (name, capacity >= 1) and list all rooms. Room names are unique.
- R2 Bookings: book a room for an interval (room_id, title, start, end). start and end are ISO 8601 datetimes.
- R3 No double booking: a booking must not overlap another booking of the same room.
  Overlap means new.start < old.end and old.start < new.end. Back-to-back bookings are allowed.
- R4 Validation: start must be before end, the title must not be empty, and the room must exist.
- R5 List the bookings of one room, sorted by start.
- R6 Cancel a booking by id.
- R7 Health check.

## HTTP API (fixed paths, JSON bodies)
- GET /health -> 200 {"status": "ok"}
- POST /rooms -> 201 Room | 409 name taken | 422 invalid body
- GET /rooms -> 200 list of Room
- POST /bookings -> 201 Booking | 404 room not found | 409 overlap | 422 invalid body or start >= end
- GET /rooms/{room_id}/bookings -> 200 list of Booking | 404 room not found
- DELETE /bookings/{booking_id} -> 204 | 404 booking not found
- Room = {id, name, capacity}. Booking = {id, room_id, title, start, end}.

## Technical constraints
- Python 3.12, FastAPI, Pydantic v2 and SQLite through the standard library module sqlite3.
  No ORM and no other runtime dependencies (see requirements.txt).
- Package `booking` in src/booking/, tests in tests/. Fixed module layout:
  - src/booking/models.py: Pydantic models RoomCreate, Room, BookingCreate, Booking.
  - src/booking/storage.py: class Storage (all SQL) and the exceptions NotFoundError and ConflictError.
  - src/booking/api.py: create_app(db_path: str) -> FastAPI, plus app = create_app(os.environ.get("BOOKING_DB", "booking.db")).
- Run locally: uvicorn booking.api:app --app-dir src --port 8000
- Tests use create_app(":memory:") with fastapi.testclient.TestClient.
- Every source file stays below 80 lines.
- Deployment target: one Docker container on a developer laptop, published only on 127.0.0.1.
