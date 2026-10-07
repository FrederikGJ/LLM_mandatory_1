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

    @model_validator(mode='after')
    def validate_start_end(cls, self):
        if self.start >= self.end:
            raise ValueError("Start must be before end")
        return self
