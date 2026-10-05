import os

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from booking.models import Booking, BookingCreate, Room, RoomCreate
from booking.storage import ConflictError, NotFoundError, Storage


def create_app(db_path: str) -> FastAPI:
    api = FastAPI()
    storage = Storage(db_path)

    @api.exception_handler(NotFoundError)
    async def missing(request: Request, error: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @api.exception_handler(ConflictError)
    async def conflict(request: Request, error: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.post("/rooms", status_code=201)
    def create_room(data: RoomCreate) -> Room:
        return storage.create_room(data)

    @api.get("/rooms")
    def list_rooms() -> list[Room]:
        return storage.list_rooms()

    @api.post("/bookings", status_code=201)
    def create_booking(data: BookingCreate) -> Booking:
        return storage.create_booking(data)

    @api.get("/rooms/{room_id}/bookings")
    def list_bookings(room_id: int) -> list[Booking]:
        return storage.list_bookings(room_id)

    @api.delete("/bookings/{booking_id}", status_code=204)
    def delete_booking(booking_id: int) -> Response:
        storage.delete_booking(booking_id)
        return Response(status_code=204)

    return api


app = create_app(os.environ.get("BOOKING_DB", "booking.db"))

