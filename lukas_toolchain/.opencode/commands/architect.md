---
description: 1/9 Architecture - overview, OpenAPI contract, ADRs (architect, llm-a, main)
agent: architect
subtask: false
---
SPEC.md (product owner's input, do not change it):
@SPEC.md

Write these four files with the write tool, one call per file, in this order:
1. docs/architecture/overview.md (max 30 lines): components (src/booking/api.py, storage.py, models.py, the SQLite file)
   and their responsibilities, deployment topology (one Docker container, port 8000 published only on 127.0.0.1,
   SQLite file in a volume), and the constraints from SPEC.md.
2. docs/architecture/openapi.yaml (max 70 lines): valid YAML, OpenAPI 3.1, exactly the six paths in SPEC.md with their
   status codes, and the schemas RoomCreate, Room, BookingCreate, Booking.
3. docs/architecture/adr/ADR-001-fastapi.md (max 12 lines): sections Context, Decision, Consequences.
4. docs/architecture/adr/ADR-002-sqlite.md (max 12 lines): sections Context, Decision, Consequences.
