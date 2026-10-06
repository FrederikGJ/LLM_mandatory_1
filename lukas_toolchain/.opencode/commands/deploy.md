---
description: 9/9 Deployment validation - Dockerfile, checklist, container build (deploy, llm-b, main)
agent: deploy
subtask: false
---
Write these two files with the write tool, one call per file:
1. Dockerfile (max 15 lines): FROM python:3.12-slim; WORKDIR /app; copy requirements.txt and
   pip install --no-cache-dir -r requirements.txt; copy src/ to /app/src; ENV BOOKING_DB=/data/booking.db;
   RUN mkdir -p /data; EXPOSE 8000;
   CMD ["uvicorn", "booking.api:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"].
2. docs/reports/deploy-check.md (max 25 lines): a deployment checklist with "- [ ]" items:
   build (docker build -t booking-demo .), run (docker run --rm -p 127.0.0.1:8000:8000 -v booking-data:/data booking-demo),
   environment variables, port published only on 127.0.0.1, health check (curl http://127.0.0.1:8000/health),
   backup of the /data volume, and a section "## Build result" that says "not run yet".
