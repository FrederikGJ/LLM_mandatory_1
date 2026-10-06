---
description: Step 1/3. architect -> tech_lead. Stop for review of the plan.
agent: orchestrator
---
Run these steps in order. For each step call the task tool once with the given subagent_type,
and copy the step text below as the task prompt word for word (the subagent cannot see this conversation).

1. subagent_type: architect
   SPEC.md is in the repository root. Read SPEC.md. Then write these files for the meeting room booking API it describes:
   docs/architecture/overview.md (components, responsibilities, deployment topology, constraints),
   docs/architecture/openapi.yaml (exactly the endpoints in SPEC.md),
   docs/architecture/adr/ADR-001-fastapi.md and docs/architecture/adr/ADR-002-sqlite.md.
   Then commit them.

2. subagent_type: tech_lead
   Read SPEC.md and docs/architecture/overview.md. Split the implementation of the booking API in SPEC.md into three tickets:
   docs/tickets/T-001-models.md: src/booking/models.py, Assignee coder_1, depends on none.
   docs/tickets/T-002-storage.md: src/booking/storage.py, Assignee coder_1, depends on T-001.
   docs/tickets/T-003-api.md: src/booking/api.py, Assignee coder_2, depends on T-001 and T-002.
   Each ticket describes the code to write in that file. Then commit them.

When both steps are done, list the changed files and stop.
