from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class RoomCreate(BaseModel):
    name: str
    capacity: int = Field(ge=1)


class Room(RoomCreate):
    id: int


class BookingCreate(BaseModel):
    room_id: int
    title: str = Field(min_length=1)
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def check_interval(self) -> "BookingCreate":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class Booking(BookingCreate):
    id: int

