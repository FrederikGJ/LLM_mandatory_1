import sqlite3

from booking.models import Booking, BookingCreate, Room, RoomCreate


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS rooms(id INTEGER PRIMARY KEY,
                                           name TEXT UNIQUE, capacity INT);
            CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY, room_id INT, title TEXT,
                                               start TEXT, end TEXT);
        """)
        self.db.commit()

    def create_room(self, data: RoomCreate) -> Room:
        try:
            cursor = self.db.execute("INSERT INTO rooms(name,capacity) VALUES(?,?)",
                                     (data.name, data.capacity))
            self.db.commit()
        except sqlite3.IntegrityError as error:
            raise ConflictError("name taken") from error
        return self.get_room(int(cursor.lastrowid or 0))

    def get_room(self, room_id: int) -> Room:
        row = self.db.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
        if row is None:
            raise NotFoundError("room not found")
        return Room(**dict(row))

    def list_rooms(self) -> list[Room]:
        return [Room(**dict(row)) for row in self.db.execute("SELECT * FROM rooms ORDER BY id")]

    def create_booking(self, data: BookingCreate) -> Booking:
        self.get_room(data.room_id)
        start, end = data.start.isoformat(), data.end.isoformat()
        overlap = self.db.execute("SELECT id FROM bookings WHERE room_id=? AND start<? AND end>?",
                                  (data.room_id, end, start)).fetchone()
        if overlap:
            raise ConflictError("overlap")
        cursor = self.db.execute("INSERT INTO bookings(room_id,title,start,end) VALUES(?,?,?,?)",
                                 (data.room_id, data.title, start, end))
        self.db.commit()
        return Booking(id=int(cursor.lastrowid or 0), **data.model_dump())

    def list_bookings(self, room_id: int) -> list[Booking]:
        self.get_room(room_id)
        rows = self.db.execute("SELECT * FROM bookings WHERE room_id=? ORDER BY start", (room_id,))
        return [Booking(**dict(row)) for row in rows]

    def delete_booking(self, booking_id: int) -> None:
        cursor = self.db.execute("DELETE FROM bookings WHERE id=?", (booking_id,))
        self.db.commit()
        if cursor.rowcount == 0:
            raise NotFoundError("booking not found")

