# Shared interface contract (product-owner input, read-only)
Both coding workers implement this contract independently; no dynamic method names.
- models.py: RoomCreate(name: str, capacity: int >= 1); Room adds id: int.
- models.py: BookingCreate(room_id: int, title: str non-empty, start: datetime, end: datetime).
- models.py: Booking adds id: int. BookingCreate validates start < end.
- storage.py: NotFoundError(Exception), ConflictError(Exception).
- Storage.__init__(self, db_path: str) -> None
- Storage.create_room(self, data: RoomCreate) -> Room; ConflictError on duplicate name.
- Storage.get_room(self, room_id: int) -> Room; NotFoundError if missing.
- Storage.list_rooms(self) -> list[Room]
- Storage.create_booking(self, data: BookingCreate) -> Booking; missing room -> NotFoundError;
  overlap -> ConflictError. Overlap: new.start < old.end AND old.start < new.end, same room only.
- Storage.list_bookings(self, room_id: int) -> list[Booking]; sorted; missing room -> NotFoundError.
- Storage.delete_booking(self, booking_id: int) -> None; missing booking -> NotFoundError.
- api.py: create_app(db_path: str) -> FastAPI; independent Storage per application.
- api.py: app = create_app(os.environ.get("BOOKING_DB", "booking.db"))
- API maps NotFoundError to HTTP 404 and ConflictError to 409. Pydantic validation -> 422.

