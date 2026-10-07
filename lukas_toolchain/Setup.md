# OpenCode Setup

Setup guide for the OpenCode toolchain (branch `last-try`). It lets a third party run the same demo workflow
as the reference run and compare results and failures. The toolchain connects to the two local LLM endpoints
from `llm_backend/`:

| Endpoint | Model | URL | Roles |
|---|---|---|---|
| `llm-a` | Qwen2.5-3B-Instruct (Q4_K_M) | `http://127.0.0.1:8081/v1` | architect, tech_lead |
| `llm-b` | Qwen2.5-1.5B-Instruct (Q4_K_M) | `http://127.0.0.1:8082/v1` | coder_1, coder_2, tester, docs, deploy |

## Prerequisites

- Docker with Compose (the backend and the deployment check). About 7 GB free RAM for the two containers
  (limits: llm-a 4 GB, llm-b 3 GB) and about 3.5 GB disk for the model files
- Git, `curl` and Python 3.12 or newer (`python3`, or set `PYTHON="py -3.12"` on Windows)
- Node.js (Windows install of OpenCode)
- **Windows only:** Git for Windows and Windows Terminal

## 1. Install OpenCode

The reference run used OpenCode **1.18.34**. Use the same version to get comparable results.

**Linux / macOS**
```bash
curl -fsSL https://opencode.ai/install | bash
```

**Windows**
```bash
npm install -g opencode-ai@1.18.34
```

Check the installation:
```bash
opencode --version        # 1.18.34
```

## 2. Choose the right terminal

All scripts are bash scripts.

**Linux / macOS:** use any terminal.

**Windows:** use Git Bash *inside Windows Terminal*. Open Windows Terminal (PowerShell) and run:
```powershell
& "C:\Program Files\Git\bin\bash.exe" -l
```

> ⚠️ Do **not** use the standard "Git Bash" window (mintty). OpenCode can't read
> keyboard input there and fails with `EUNKNOWN: unknown error, read`.
> Check with `echo "$TERM_PROGRAM"`. It must **not** print `mintty`.

## 3. Configure and start the two endpoints

The backend is set up as described in `llm_backend/README.md`. This toolchain needs four values in
`llm_backend/.env` that differ from the defaults in `.env.example`:

```bash
# llm_backend/.env (only these lines change; keep the API keys and the rest)
MODEL_A_FILE=qwen2.5-3b-instruct-q4_k_m.gguf
MODEL_A_URL=https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
MODEL_A_ALIAS=qwen2.5-3b-instruct
MODEL_B_FILE=qwen2.5-1.5b-instruct-q4_k_m.gguf
MODEL_B_URL=https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf
MODEL_B_ALIAS=qwen2.5-1.5b-instruct
LLM_CTX_SIZE=16384
LLM_A_MAX_TOKENS=2048
LLM_B_MAX_TOKENS=2048
```

Why:
- **Instruct models instead of Coder models:** the Qwen2.5-Coder 3B/1.5B models did not make native tool calls
  through llama.cpp, so OpenCode could not write files with them.
- **16384 context:** OpenCode's system prompt and tool definitions alone filled most of 8192 tokens.
- **2048 output tokens on llm-b:** a coder writes a whole file in one tool call; 1024 cut the file off.

Start (or restart) the backend so the new models are downloaded and loaded:
```bash
cd llm_backend
docker compose up -d
docker compose ps          # llm-a and llm-b must be "healthy" (the first start downloads the models)
```

Check the endpoints and model IDs from the toolchain folder:
```bash
cd ../lukas_toolchain
git switch last-try
./scripts/check-models.sh
```

Expected output:
```
llm-a  (127.0.0.1:8081): qwen2.5-3b-instruct
llm-b  (127.0.0.1:8082): qwen2.5-1.5b-instruct
```

The IDs must match the model names under `"models"` in `opencode.json`. Both endpoints are bound to
127.0.0.1 and require the API keys from `llm_backend/.env`; `scripts/load-keys.sh` exports them for OpenCode,
so no key is stored in `opencode.json` or in git.

## 4. Test that OpenCode can write files

OpenCode only runs inside a separate demo repo (its own git repo, no remote, push blocked), never in
`llm_mandatory_1`. Create one and open the TUI in it:

```bash
bash scripts/init_demo.sh runs/smoke/demo     # copies template/, git init, creates .venv
bash scripts/run-opencode.sh runs/smoke/demo
```

In the input field at the bottom:

1. Type `/models` and press Enter. Both models must appear. Close the list with `Esc`.
2. Type: `Create the file hello.txt with the content: hej`
3. Check in another terminal: `git -C runs/smoke/demo status` shows `hello.txt`.
   (Edits are allowed without a prompt in `opencode.json`; control is the review of git diffs, see step 7.)

## 5. Agents (roles bound to endpoints)

Everything is configured in `lukas_toolchain/`, which `scripts/common_opencode.sh` points OpenCode to with
`OPENCODE_CONFIG` and `OPENCODE_CONFIG_DIR`:

| File | Purpose |
|---|---|
| `opencode.json` | the two providers (`llm-a`, `llm-b`) as OpenAI-compatible endpoints, global permissions |
| `.opencode/agents/*.md` | one agent per role; `model:` binds it to an endpoint; `tools:` leaves only read/write/edit |
| `.opencode/commands/*.md` | one command per workflow step, bound to its agent with `agent:`; input inlined with `@file` and `` !`cmd` `` |
| `.opencode/plugins/git-guard.js` | picks the branch per command, merges the coder branches, commits each step |
| `template/` | the demo project: `SPEC.md` (meeting-room booking API), `CONTRACT.md` (module contract), `AGENTS.md` (rules) |

| Agent | Endpoint | Commands | Branch | Writes |
|---|---|---|---|---|
| architect | llm-a | `/architect` | main | `docs/architecture/` (overview, openapi.yaml, 2 ADRs) |
| tech_lead | llm-a | `/tickets` | main | `docs/tickets/T-001..T-003` |
| coder_1 | llm-b | `/code-models`, `/code-storage` | coder_1 | `src/booking/models.py`, `storage.py` |
| coder_2 | llm-b | `/code-api` | coder_2 | `src/booking/api.py` |
| tester | llm-b | `/test`, `/quality` | main | `tests/test_api.py`, `docs/reports/quality-report.md` |
| docs | llm-b | `/docs` | main | `README.md`, `docs/api-usage.md`, `docs/runbook.md` |
| deploy | llm-b | `/deploy`, `/deploy-check` | main | `Dockerfile`, `docs/reports/deploy-check.md` |

To move a role to the other endpoint, change `model:` in its agent file. Nothing else needs to change.

## 6. Verify the routing

Run one command in the smoke demo and check which endpoint served it:
```bash
source scripts/common.sh; source scripts/load-keys.sh; source scripts/common_opencode.sh
opencode_in_demo runs/smoke/demo run --print-logs --log-level INFO --command architect < /dev/null 2> routing.log
grep 'message=stream' routing.log | grep -o 'providerID=[^ ]*\|agent=[^ ]*' | paste - - | sort -u
```

Expected: `providerID=llm-a  agent=architect`. In the full run (step 7) the same grep over
`runs/<run-id>/logs/*.opencode.log` shows llm-a for architect/tech_lead and llm-b for all other agents.
`docker compose logs llm-a` / `llm-b` in `llm_backend/` show the matching requests on each container.

## 7. Demo workflow

Unattended run of all steps in a new demo repo:
```bash
bash run_all.sh
```

The script creates `runs/run-<timestamp>/demo`, runs the ten commands in order with `opencode run --command`,
and writes `runs/run-<timestamp>/summary.md`. The reference run took about 35 minutes on CPU.

| Phase | Commands |
|---|---|
| plan | `/architect`, `/tickets` |
| implement | `/code-models`, `/code-storage` (coder_1), `/code-api` (coder_2) |
| finish | `/test`, `/quality`, `/docs`, `/deploy`, `/deploy-check` |

Options (environment variables):

| Variable | Default | Effect |
|---|---|---|
| `REVIEW=1` | 0 | stop after the plan and after the code, show `git diff --stat`, continue only on `y` (plan + diff review) |
| `RETRIES` | 1 | new attempts per step if its expected output is missing |
| `KEEP_GOING` | 1 | a step that still fails is recorded as `opgivet` and the run continues; `0` stops the run |
| `STEP_TIMEOUT` | 5400 | max seconds per step |
| `RUN_ID` | `run-<timestamp>` | name of the run folder |
| `RESUME_RUN=<run-id> START_AT=implement\|finish` | | continue an interrupted run in the same demo repo |

Interactive alternative: `bash scripts/init_demo.sh runs/manual/demo && bash scripts/run-opencode.sh runs/manual/demo`,
then type the commands above one by one and review `git -C runs/manual/demo diff` between them.

## 8. Read and compare the results

Everything a run produces stays in `runs/<run-id>/` (ignored by git):

| Path | Content |
|---|---|
| `summary.md` | the results (see below) |
| `logs/<command>.log` | the agent's final answer per step |
| `logs/<command>.opencode.log` | OpenCode's own log (endpoint, agent, tool errors) |
| `logs/pytest.txt`, `ruff.txt`, `import.txt`, `docker-build.txt` | full output of the checks |
| `demo/` | the demo repo; `git -C demo log --all --graph --oneline` shows each step as a commit |
| `demo/.git/git-guard.jsonl` | branch switches, merges and commits made by the plugin |

`summary.md` (headings in Danish) has four tables:

| Section | Shows |
|---|---|
| `## Kommandoer` | per attempt: seconds, exit code, missing output (`alt leveret` = all expected files exist; `opgivet` = gave up) |
| `## Kommandoer set fra git-guard` | per command: branch, seconds, the files the agent actually changed |
| `## Kvalitet (kørt af run_all.sh)` | pytest, ruff, import of `booking.api`, `docker build`, run by the script, not reported by an LLM |
| `## Git-tjek` | the group's git policy: no remote, only main/coder_1/coder_2, nothing uncommitted, `llm_mandatory_1` untouched |

Compare two or more runs side by side:
```bash
for s in runs/run-*/summary.md; do
  echo "== $s"
  sed -n '/^## Kommandoer$/,/^## Kommandoer set/p;/^## Kvalitet/,/^## Git-tjek/p' "$s" | grep '^|'
done
```

Outputs are not identical between runs (sampling), but the structure is: the same commands, branches, files
and checks. Compare which steps delivered, how many attempts they needed, and which checks pass.

### Reference run (run-20261006-200853, OpenCode 1.18.34, models and settings as in step 3)

| Result | Value |
|---|---|
| Total time | 2076 s (all ten commands ran; no hangs, no orchestrator loops) |
| Steps needing a retry | `/code-models`, `/test`, `/docs` (`/docs` gave up: no `README.md`) |
| Files written per step | architecture 4, tickets 3, code 2 of 3 (`models.py`, `storage.py`), tests, quality report, Dockerfile, checklist, 2 docs |
| pytest / ruff / import / docker build | all fail |
| Git-tjek | all ok |

Failure modes seen in the reference run, in the order they hit (look for the same in your run):
1. **Tool call cut off:** `/code-api` wrote nothing; `code-api.opencode.log` shows
   `Invalid input for tool write: JSON parsing failed ... Unterminated string` (the file did not fit in one answer).
2. **File format:** `models.py` and `storage.py` are wrapped in markdown code fences, so they are not valid Python.
3. **Scope:** agents wrote outside their own file (tech_lead and tester into `src/booking/api.py`).
4. **Contract and instructions ignored:** `storage.py` uses dicts instead of SQLite; the Dockerfile is one line;
   `deploy-check.md` reports an image id although the build failed.
5. **Detection:** every failure above is caught by the script's checks in `## Kvalitet`, not by the agents' own reports.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `EUNKNOWN: unknown error, read` | Running in the standard Git Bash window (mintty) on Windows | Use Git Bash via Windows Terminal (see step 2) |
| `FEJL: intet svar fra 127.0.0.1:8081` | Backend is not running | `docker compose up -d` in `llm_backend/` |
| `FEJL: LLM_A_API_KEY mangler` | `llm_backend/.env` is missing or not filled in | See `llm_backend/README.md` |
| `check-models.sh` shows `qwen2.5-coder-*` | `.env` still has the default models | Set the values from step 3 and `docker compose up -d` again |
| `Agent not found` / unknown command | Wrong branch | `git switch last-try` |
| `intet demo-repo endnu` | `run-opencode.sh` without a demo repo | `bash scripts/init_demo.sh runs/<name>/demo` first |
| `$'\r': command not found` | The script has Windows line endings | Save as LF, and add `*.sh text eol=lf` to `.gitattributes` |
| `docker-build` row missing in `summary.md` | `docker` not in PATH | Install Docker or ignore; `/deploy-check` then records the error |
| A step shows `timeout` | Slow CPU | Raise `STEP_TIMEOUT` |

## Known limitations

- **CPU only.** Prompt processing is roughly 15-40 tokens/s and generation 3-9 tokens/s, so each step takes
  minutes. The first request per agent is the slowest; llama-server caches the prompt prefix for later turns.
- **coder_1 and coder_2 share `llm-b`,** which runs one request at a time (`--parallel 1`). The work is split
  into separate branches and tickets, but the requests run one after the other.
- **Small models.** With a 3B and a 1.5B model the workflow completes, but the code does not pass its checks
  (see the reference run). The structure is reproducible; the code quality is not yet usable.
- **Human-written contract.** `template/CONTRACT.md` fixes the module interfaces so the two coders can work on
  separate branches; the models could not agree on interfaces on their own.
