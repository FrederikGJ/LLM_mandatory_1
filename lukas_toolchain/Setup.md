# OpenCode Setup

Setup guide for the OpenCode toolchain. It connects to the two local LLM endpoints
from `llm_backend/`:

| Endpoint | Model | URL |
|---|---|---|
| `llm-a` (architect / tech lead) | Qwen2.5-Coder-3B | `http://127.0.0.1:8081/v1` |
| `llm-b` (coders / tester / docs / deploy) | Qwen2.5-Coder-1.5B | `http://127.0.0.1:8082/v1` |

## Prerequisites

- The backend is running: `docker compose ps` in `llm_backend/` shows `llm-a` and `llm-b` as `healthy`
  (see `llm_backend/README.md`)
- Git, `curl` and `python3`
- **Windows only:** Git for Windows and Windows Terminal

## 1. Install OpenCode

**Linux / macOS**
```bash
curl -fsSL https://opencode.ai/install | bash
```

**Windows** (requires Node.js)
```bash
npm install -g opencode-ai
```

Check the installation:
```bash
opencode --version
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

Optional: add a permanent profile in Windows Terminal (Settings → Add a new profile):
command line `"C:\Program Files\Git\bin\bash.exe" -i -l`.


## 3. Check the endpoints and model IDs

```bash
cd LLM_mandatory_1/lukas_toolchain
./scripts/check-models.sh
```

Expected output:
```
llm-a  (127.0.0.1:8081): qwen2.5-coder-3b
llm-b  (127.0.0.1:8082): qwen2.5-coder-1.5b
```

The IDs must match the model names under `"models"` in `opencode.json`.

## 4. Test that OpenCode can run and edit files

```bash
git status                 # clean starting point
./scripts/run-opencode.sh
```

OpenCode's interface opens. In the input field at the bottom:

1. Type `/models` and press Enter. Both models must appear. Close the list with `Esc`.
2. Type: `Create the file hello.txt with the content: hej`
3. OpenCode asks for permission to write the file. Approve it.
4. Check in another terminal with `git status`. `hello.txt` should appear.

## 5. Agents (roles bound to endpoints)

TODO

## 6. Verify the routing

TODO

## 7. Demo workflow

TODO

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `EUNKNOWN: unknown error, read` | Running in the standard Git Bash window (mintty) on Windows | Use Git Bash via Windows Terminal (see step 2) |
| `FEJL: intet svar fra 127.0.0.1:8081` | Backend is not running | `docker compose up -d` in `llm_backend/` |
| `FEJL: LLM_A_API_KEY mangler` | `llm_backend/.env` is missing or not filled in | See `llm_backend/README.md` |
| `$'\r': command not found` | The script has Windows line endings | Save as LF, and add `*.sh text eol=lf` to `.gitattributes` |
| The answer is followed by a `<template>` block with "Objective / Work State / Next Move" | Context (8192 tokens) is almost full, so OpenCode's auto-compaction kicks in | See "Known limitations" |

## Known limitations

- **Context of 8192 tokens.** OpenCode's own system prompt and tool definitions take up a large share
  of it. Keep prompts and agent instructions short.
- **Slow first answer.** Prompt evaluation on CPU takes a while. Later turns are faster
  because llama-server caches the prompt prefix.
- **coder-1 and coder-2 share `llm-b`,** which runs one request at a time (`--parallel 1`).
  The work is split into separate tickets, but the requests run one after the other.