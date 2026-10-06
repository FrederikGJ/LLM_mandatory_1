---
description: Step 2/3. coder_1 and coder_2, each on its own branch. Stop for review of the code.
agent: orchestrator
---
Run these steps in order. For each step call the task tool once with the given subagent_type,
and copy the step text below as the task prompt word for word (the subagent cannot see this conversation).
Do not run any git commands yourself; the toolchain switches branches.

1. subagent_type: coder_1
   Implement the tickets in docs/tickets/ with Assignee coder_1, in dependency order.
   Write the code in the file each ticket names. Commit after each ticket.

2. subagent_type: coder_2
   Implement the tickets in docs/tickets/ with Assignee coder_2.
   Write the code in the file the ticket names. Commit when done.

When both steps are done, list the changed files and stop.
