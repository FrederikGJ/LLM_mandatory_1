---
description: 2/9 Tickets - three ordered tickets (tech_lead, llm-a, main)
agent: tech_lead
subtask: false
---
SPEC.md:
@SPEC.md

CONTRACT.md (module contract):
@CONTRACT.md

Write three tickets with the write tool, one call per file. Use exactly this layout (max 20 lines per ticket):

# <id>: <title>
Assignee: <assignee>
Depends on: <ids or none>
Status: todo
## Scope
- the one file to write and what it must contain (from CONTRACT.md)
## Out of scope
- what belongs to the other tickets
## Acceptance criteria
- [ ] testable criteria taken from SPEC.md (R1-R7) and CONTRACT.md

The tickets:
1. docs/tickets/T-001-models.md: T-001 Models. File src/booking/models.py. Assignee coder_1. Depends on none.
2. docs/tickets/T-002-storage.md: T-002 Storage. File src/booking/storage.py. Assignee coder_1. Depends on T-001.
3. docs/tickets/T-003-api.md: T-003 API. File src/booking/api.py. Assignee coder_2. Depends on T-001, T-002
   (built in parallel on its own branch against CONTRACT.md).
