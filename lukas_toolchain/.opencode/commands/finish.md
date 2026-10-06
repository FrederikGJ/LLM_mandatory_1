---
description: Step 3/3. tester -> docs -> deploy (the coder branches are merged into main first).
agent: orchestrator
---
Run these steps in order. For each step call the task tool once with the given subagent_type,
and copy the step text below as the task prompt word for word (the subagent cannot see this conversation).

1. subagent_type: tester
   Write pytest tests in tests/test_api.py for src/booking/api.py using TestClient(create_app(":memory:")).
   Run pytest -q and ruff check . Write docs/reports/quality-report.md with test results, static checks,
   known limitations and risks. Then commit.

2. subagent_type: docs
   Write README.md (setup and run), docs/api-usage.md and docs/runbook.md from src/booking/ and docs/architecture/.
   Then commit.

3. subagent_type: deploy
   Write the Dockerfile and docs/reports/deploy-check.md. Run docker build -t booking-demo . and record the result
   in docs/reports/deploy-check.md. Then commit.

When all steps are done, list the changed files and stop.
