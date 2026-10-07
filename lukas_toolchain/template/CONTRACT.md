# Module contract

Internal Python contract for the booking package. Human-written input like SPEC.md; agents must not change it.
It exists so coder_1 and coder_2 can build their files in parallel on separate branches and still fit together.

## src/booking/models.py (Pydantic v2, `from pydantic import BaseModel, Field`)
- RoomCreate: name: str = Field(min_length=1), capacity: int = Field(ge=1)
- Room(RoomCreate): id: int
- BookingCreate: room_id: int, title: str = Field(min_length=1), start: datetime, end: datetime
- Booking(BookingCreate): id: int

## src/booking/storage.py (standard library sqlite3 only)
- class NotFoundError(Exception) and class ConflictError(Exception)
- class Storage, constructor Storage(db_path: str):
  self.conn = sqlite3.connect(db_path, check_same_thread=False), then CREATE TABLE IF NOT EXISTS
  rooms(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, capacity INTEGER NOT NULL) and
  bookings(id INTEGER PRIMARY KEY AUTOINCREMENT, room_id INTEGER NOT NULL, title TEXT NOT NULL,
  start_at TEXT NOT NULL, end_at TEXT NOT NULL). Datetimes are stored with .isoformat().
- create_room(data: RoomCreate) -> Room: raises ConflictError if the name is taken (sqlite3.IntegrityError)
- list_rooms() -> list[Room]
- create_booking(data: BookingCreate) -> Booking: raises NotFoundError if the room does not exist,
  ConflictError if it overlaps a booking of the same room (new.start < old.end_at and old.start_at < new.end)
- list_bookings(room_id: int) -> list[Booking]: sorted by start; raises NotFoundError if the room does not exist
- delete_booking(booking_id: int) -> None: raises NotFoundError if the booking does not exist

## src/booking/api.py (FastAPI)
- imports: `from booking.models import RoomCreate, Room, BookingCreate, Booking` and
  `from booking.storage import Storage, NotFoundError, ConflictError`
- create_app(db_path: str) -> FastAPI: creates one Storage(db_path) and these routes:
  GET /health -> 200 {"status": "ok"}
  POST /rooms -> 201 Room; ConflictError -> 409
  GET /rooms -> 200 list[Room]
  POST /bookings -> 201 Booking; start >= end -> 422; NotFoundError -> 404; ConflictError -> 409
  GET /rooms/{room_id}/bookings -> 200 list[Booking]; NotFoundError -> 404
  DELETE /bookings/{booking_id} -> 204; NotFoundError -> 404
  Errors are raised as fastapi.HTTPException(status_code=..., detail=...).
- module level: app = create_app(os.environ.get("BOOKING_DB", "booking.db"))
