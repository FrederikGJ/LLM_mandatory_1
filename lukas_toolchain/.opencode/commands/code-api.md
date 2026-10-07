---
description: 5/9 T-003 src/booking/api.py (coder_2, llm-b, branch coder_2)
agent: coder_2
subtask: false
---
Your ticket:
@docs/tickets/T-003-api.md

The module contract (authoritative if the ticket disagrees). models.py and storage.py are written by coder_1
on another branch at the same time; import them exactly as the contract says:
@CONTRACT.md

Write src/booking/api.py with one write tool call. Python 3.12, FastAPI, type hints, below 80 lines.
Use the paths and status codes from the contract. Write no other file.
