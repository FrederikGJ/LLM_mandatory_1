# OpenCode Toolchain - Lukas

Lukas' toolchain for LLM_mandatory_1. OpenCode version: 1.18.34. Setup: see `Setup.md`.

## Branch `last-try`: one command per step, no orchestrator

The first version (branch `lukas-opencode`, evaluated in `docs/evaluation/run-1.md`) used a 3B orchestrator
that delegated to subagents with the task tool. It never produced code: the orchestrator started tasks in
parallel, repeated finished steps and looped, the tech lead wrote malformed tickets, and the 1.5B coders
returned without writing to `src/`.

`last-try` keeps OpenCode's own building blocks but removes the parts the small models could not handle:

| Building block | Where | What it does |
|---|---|---|
| Agents bound to endpoints | `.opencode/agents/*.md` (`model:`) | architect, tech_lead -> `llm-a` (3B); coder_1, coder_2, tester, docs, deploy -> `llm-b` (1.5B) |
| One command per step | `.opencode/commands/*.md` (`agent:`) | each command runs its role agent directly; no orchestrator, no task tool |
| Inlined input | `@file` and `` !`cmd` `` in the commands | the agent gets SPEC.md, its ticket, the contract, earlier code and pytest/ruff/docker output in the prompt, so it needs no read/bash calls |
| Small tool set | `tools:` in each agent | only read/write/edit; bash, grep, glob, list, task, todo, web are removed (smaller prompt, fewer wrong calls) |
| Git policy | `.opencode/plugins/git-guard.js` | on `command.execute.before`: picks the branch (coder_1 / coder_2 / main + merge); on `session.idle`: commits the agent's files |
| Module contract | `template/CONTRACT.md` | human-written, like SPEC.md: exact class/function names so coder_1 and coder_2 can work in parallel on separate branches |

### Steps (in order)

| # | Command | Agent (endpoint) | Branch | Output |
|---|---|---|---|---|
| 1 | `/architect` | architect (llm-a) | main | `docs/architecture/overview.md`, `openapi.yaml`, `adr/ADR-001..002` |
| 2 | `/tickets` | tech_lead (llm-a) | main | `docs/tickets/T-001..T-003` |
| 3 | `/code-models` | coder_1 (llm-b) | coder_1 | `src/booking/models.py` |
| 4 | `/code-storage` | coder_1 (llm-b) | coder_1 | `src/booking/storage.py` |
| 5 | `/code-api` | coder_2 (llm-b) | coder_2 | `src/booking/api.py` |
| 6 | `/test` | tester (llm-b) | main (coders merged) | `tests/test_api.py` |
| 7 | `/quality` | tester (llm-b) | main | runs pytest + ruff, writes `docs/reports/quality-report.md` |
| 8 | `/docs` | docs (llm-b) | main | `README.md`, `docs/api-usage.md`, `docs/runbook.md` |
| 9 | `/deploy`, `/deploy-check` | deploy (llm-b) | main | `Dockerfile`, `docs/reports/deploy-check.md` with the `docker build` result |

### Run

Unattended (all steps, `summary.md` in `runs/<run-id>/`):
```bash
bash run_all.sh                 # REVIEW=1 pauses with diffs after the plan and after the code
```
Interactive in the TUI (type the commands above one by one, review `git diff` in between):
```bash
bash scripts/init_demo.sh runs/manual/demo
bash scripts/run-opencode.sh runs/manual/demo
```

### Deliberate deviations (for the evaluation)
- `CONTRACT.md` is extra human input. Without it the two coders had no shared interface across branches.
- The workflow order lives in commands + `run_all.sh`, not in an LLM orchestrator.
- pytest, ruff and docker build are run by OpenCode's command templates (`` !`cmd` ``), not by the 1.5B model via bash.
