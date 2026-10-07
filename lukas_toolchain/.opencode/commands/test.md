---
description: 6/9 Tests - tests/test_api.py (tester, llm-b, main after merge)
agent: tester
subtask: false
---
The code under test:
@src/booking/api.py

The module contract:
@CONTRACT.md

Write tests/test_api.py with one write tool call, below 60 lines, max 7 tests. Use
`from fastapi.testclient import TestClient` and `from booking.api import create_app`, and create a fresh
`client = TestClient(create_app(":memory:"))` inside each test. Cover:
health 200; create room 201 and list rooms; duplicate room name 409; create booking 201;
overlapping booking 409 while a back-to-back booking is 201; start >= end gives 422; delete booking 204 then 404.
