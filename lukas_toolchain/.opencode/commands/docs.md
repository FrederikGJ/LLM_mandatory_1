---
description: 8/9 Docs - README, API usage, runbook (docs, llm-b, main)
agent: docs
subtask: false
---
The API code:
@src/booking/api.py

The architecture overview:
@docs/architecture/overview.md

Write these three files with the write tool, one call per file:
1. README.md (max 30 lines): what the service is; setup (python -m venv .venv, pip install -r requirements-dev.txt);
   run (uvicorn booking.api:app --app-dir src --port 8000); tests (python -m pytest -q); links to docs/api-usage.md,
   docs/runbook.md and docs/architecture/.
2. docs/api-usage.md (max 40 lines): each endpoint from the code with method, path, body, status codes,
   and one curl example per endpoint against http://127.0.0.1:8000.
3. docs/runbook.md (max 25 lines): start/stop (local and Docker), configuration (env var BOOKING_DB),
   health check (GET /health), backup (copy the SQLite file), common failures.
