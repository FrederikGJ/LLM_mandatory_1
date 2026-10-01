Role: tech lead.
Break the architecture into small tickets. Every ticket must be implementable alone by one developer in one
file, using only SPEC.md, docs/arch/components.md and the ticket itself.
Start the file with "# Tickets". Every ticket has exactly five lines:
## T-xx: <short title>
- Owner: <role> | File: <path> | Depends on: <tickets or none>
- Scope: <what to implement, with the exact signatures from docs/arch/components.md>
- Out of scope: <what belongs to other tickets>
- Done when: <the SPEC requirements (R1-R7) covered>; ruff and mypy pass
