#!/usr/bin/env bash
# Uovervåget kørsel af hele workflowet med OpenCode's egne byggesten (last-try):
#   hvert trin er en OpenCode-kommando i .opencode/commands/, bundet direkte til sin rolle-agent
#   (ingen orchestrator). Kommandoerne får deres input inlinet med @fil og !`kommando`.
#   Plugin'et .opencode/plugins/git-guard.js vælger branch pr. kommando (coder_1/coder_2/main),
#   merger og committer agenternes filer, så LLM'en aldrig selv rører git.
# Dette script opretter kun demo-repoet, starter kommandoerne i rækkefølge og samler resultatet i summary.md.
#   plan:      /architect /tickets
#   implement: /code-models /code-storage (coder_1)  /code-api (coder_2)
#   finish:    /test /quality /docs /deploy /deploy-check
#
# Brug:  bash run_all.sh                 ny kørsel i runs/run-<tidspunkt>/, uden pauser
#        REVIEW=1 bash run_all.sh        stop med diff til review efter /plan og efter /implement
#        RUN_ID=run-a bash run_all.sh    eget navn på kørslen
#        STEP_TIMEOUT=7200 bash run_all.sh  maks. sekunder pr. kommando (standard 5400 = 90 min)
#        RETRIES=0 bash run_all.sh       ingen nye forsøg (standard: 1 nyt forsøg pr. kommando, hvis output mangler)
#        KEEP_GOING=0 bash run_all.sh    stop ved første trin, der fejler (standard: notér det og fortsæt)
#        RESUME_RUN=<run-id> START_AT=implement bash run_all.sh
#                                        fortsæt en afbrudt kørsel fra /implement (eller finish) i samme demo-repo
# Interaktivt i TUI'en i stedet:  bash scripts/init_demo.sh runs/<navn>/demo && bash scripts/run-opencode.sh
set -euo pipefail
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/scripts/common.sh"
# shellcheck source=scripts/load-keys.sh
source "$TC_DIR/scripts/load-keys.sh"
# shellcheck source=scripts/common_opencode.sh
source "$TC_DIR/scripts/common_opencode.sh"
command -v opencode >/dev/null || die "'opencode' findes ikke i PATH"

RESUME_RUN="${RESUME_RUN:-}"
START_AT="${START_AT:-plan}"
case "$START_AT" in plan | implement | finish) ;; *) echo "FEJL: START_AT skal være plan, implement eller finish" >&2; exit 1 ;; esac
RUN_ID="${RESUME_RUN:-${RUN_ID:-run-$(date +%Y%m%d-%H%M%S)}}"
REVIEW="${REVIEW:-0}"
STEP_TIMEOUT="${STEP_TIMEOUT:-5400}"
RETRIES="${RETRIES:-1}"
KEEP_GOING="${KEEP_GOING:-1}"
RUN_DIR="$RUNS_DIR/$RUN_ID"
DEMO_DIR="$RUN_DIR/demo"
LOG_DIR="$RUN_DIR/logs"
SUMMARY="$RUN_DIR/summary.md"

demo_git() {
  git -C "$DEMO_DIR" "$@"
}

summary() {
  printf '%s\n' "$@" >>"$SUMMARY"
}

# Findes filen, eller er mappen ikke tom? Læser fra en branch, hvis den er givet (fx coder_1:src/booking/storage.py).
has_output() {
  local spec=$1 ref path
  if [[ "$spec" == *:* ]]; then
    ref=${spec%%:*}
    path=${spec#*:}
    demo_git cat-file -e "$ref:$path" 2>/dev/null
  else
    path="$DEMO_DIR/$spec"
    [[ -f "$path" ]] || [[ -d "$path" && -n "$(ls -A "$path")" ]]
  fi
}

# step <kommando> <forventet output ...>: kører kommandoen; mangler output bagefter, køres den igen
# (højst RETRIES gange). Fx når orchestratoren skriver et task-kald som tekst i stedet for at kalde værktøjet.
step() {
  local attempt=0
  while true; do
    if (step_once "$@"); then return 0; fi
    attempt=$((attempt + 1))
    if ((attempt > RETRIES)); then
      [[ "$KEEP_GOING" == "1" ]] || die "/$1 fejlede efter $attempt forsøg (se summary.md og logs/)"
      log "/$1 opgivet efter $attempt forsøg; fortsætter (KEEP_GOING=1)"
      summary "| /$1 | - | - | opgivet efter $attempt forsøg |"
      return 0
    fi
    log "/$1 fejlede; nyt forsøg $attempt af $RETRIES"
  done
}

# step_once <kommando> <forventet output ...>: ét forsøg. Kører i en subshell, så die kun stopper forsøget.
step_once() {
  local cmd=$1 rc=0 t0 secs missing="" spec
  shift
  require_demo_repo "$DEMO_DIR"
  log "Starter /$cmd"
  t0=$(date +%s)
  # stdin fra /dev/null, så opencode run ikke venter på (eller spiser) input.
  # OpenCode's egne logs (stderr, --print-logs) gemmes pr. kommando i logs/<kommando>.opencode.log.
  # timeout stopper en kommando, der hænger (exit 124), i stedet for at vente hele natten.
  timeout "$STEP_TIMEOUT" bash -c "$(declare -f venv_bin opencode_in_demo); TC_DIR='$TC_DIR'; \
    opencode_in_demo '$DEMO_DIR' run --print-logs --log-level INFO --command '$cmd' --title '$RUN_ID /$cmd'" \
    </dev/null 2>"$LOG_DIR/$cmd.opencode.log.tmp" | tee "$LOG_DIR/$cmd.log.tmp" || rc=$?
  cat "$LOG_DIR/$cmd.opencode.log.tmp" >>"$LOG_DIR/$cmd.opencode.log"
  cat "$LOG_DIR/$cmd.log.tmp" >>"$LOG_DIR/$cmd.log"
  secs=$(($(date +%s) - t0))
  require_demo_repo "$DEMO_DIR"
  # Reserve: git-guard committer ved session.idle; nåede den det ikke, committer scriptet resten.
  if [[ -n "$(demo_git status --porcelain)" ]]; then
    demo_git add -A
    demo_git commit --quiet -m "chore(run_all): /$cmd output committet af run_all (ikke af git-guard)"
  fi
  for spec in "$@"; do
    has_output "$spec" || missing+="$spec "
  done
  if [[ $rc -eq 124 ]]; then
    summary "| /$cmd | $secs | timeout | ${missing:-alt leveret} |"
    grep -E 'level=(WARN|ERROR)' "$LOG_DIR/$cmd.opencode.log" | tail -n 10 >&2 || true
    die "/$cmd ramte timeout efter ${STEP_TIMEOUT}s (logs: $LOG_DIR/$cmd.opencode.log)"
  fi
  summary "| /$cmd | $secs | $rc | ${missing:-alt leveret} |"
  if [[ $rc -ne 0 ]]; then
    grep -E 'level=(WARN|ERROR)' "$LOG_DIR/$cmd.opencode.log" | tail -n 10 >&2 || true
    die "/$cmd stoppede med exit $rc (logs: $LOG_DIR/$cmd.log og $cmd.opencode.log)"
  fi
  [[ -z "$missing" ]] || die "/$cmd leverede ikke: $missing(log: $LOG_DIR/$cmd.log)"
  log "/$cmd færdig på ${secs}s"
}

# review_stop <titel> <diff-kommandoer ...>: med REVIEW=1 vises diffen, og kørslen venter på godkendelse.
review_stop() {
  local title=$1 answer range
  shift
  [[ "$REVIEW" == "1" ]] || return 0
  echo
  echo "===== REVIEW: $title ====="
  for range in "$@"; do
    echo "--- git diff --stat $range"
    demo_git --no-pager diff --stat "$range"
  done
  echo "Hele diffen i en anden terminal:  git -C \"$DEMO_DIR\" diff <område ovenfor>"
  read -r -p "Godkend og fortsæt? [y/N] " answer
  if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
    summary "| review: $title | - | - | godkendt |"
  else
    summary "| review: $title | - | - | AFVIST |"
    die "review afvist: $title"
  fi
}

# quality_check <navn> <kommando...>: kørt af scriptet, så tallene ikke kommer fra en LLM.
quality_check() {
  local name=$1 code=0 last shown
  shift
  shown="$*"
  shown="${shown//$DEMO_DIR\//}"
  (cd "$DEMO_DIR" && "$@") >"$LOG_DIR/$name.txt" 2>&1 || code=$?
  last=$(grep -v '^\s*$' "$LOG_DIR/$name.txt" | tail -n 1 || true)
  summary "| $name | \`$shown\` | $code | ${last//|//} |"
}

bash "$TC_DIR/scripts/check-models.sh" || die "backend-tjek fejlede (se ovenfor)"
parent_head=$(git -C "$REPO_DIR" rev-parse HEAD)
parent_branch=$(git -C "$REPO_DIR" branch --show-current)
started=$(date +%s)

mkdir -p "$RUNS_DIR"
if [[ -n "$RESUME_RUN" ]]; then
  # Genoptag: samme demo-repo. Rester fra den afbrudte kommando committes af scriptet, og repoet sættes på main.
  require_demo_repo "$DEMO_DIR"
  mkdir -p "$LOG_DIR"
  if [[ -n "$(demo_git status --porcelain)" ]]; then
    demo_git add -A
    demo_git commit --quiet -m "chore(run_all): rester fra afbrudt kørsel committet ved genoptagelse"
  fi
  [[ "$(demo_git branch --show-current)" == "main" ]] || demo_git switch --quiet main
  template_head=$(demo_git rev-list --max-parents=0 HEAD)
  summary "" "## Genoptaget $(date '+%Y-%m-%d %H:%M:%S') fra /$START_AT" "" \
    "| Kommando | Sekunder | Exit | Manglende output |" "|---|---|---|---|"
else
bash "$TC_DIR/scripts/init_demo.sh" "$DEMO_DIR"
mkdir -p "$LOG_DIR"
template_head=$(demo_git rev-parse HEAD)

summary "# Summary: $RUN_ID" "" \
  "- Startet: $(date '+%Y-%m-%d %H:%M:%S')" \
  "- REVIEW: $REVIEW" \
  "- Modeller: llm-a $(grep -o '"qwen[^"]*"' "$TC_DIR/opencode.json" | head -n 1), llm-b $(grep -o '"qwen[^"]*"' "$TC_DIR/opencode.json" | sed -n 2p)" \
  "" "## Kommandoer" "" \
  "| Kommando | Sekunder | Exit | Manglende output |" \
  "|---|---|---|---|"
fi

if [[ "$START_AT" == "plan" ]]; then
  step architect docs/architecture/overview.md docs/architecture/openapi.yaml
  step tickets docs/tickets/T-001-models.md docs/tickets/T-002-storage.md docs/tickets/T-003-api.md
  review_stop "plan (arkitektur og tickets)" "$template_head..main"
fi
if [[ "$START_AT" == "plan" || "$START_AT" == "implement" ]]; then
  plan_head=$(demo_git rev-parse main)
  step code-models coder_1:src/booking/models.py
  step code-storage coder_1:src/booking/storage.py
  step code-api coder_2:src/booking/api.py
  review_stop "kode (før tests køres)" "$plan_head..coder_1" "$plan_head..coder_2"
fi

step test tests/test_api.py
step quality docs/reports/quality-report.md
step docs README.md docs/api-usage.md docs/runbook.md
step deploy Dockerfile docs/reports/deploy-check.md
step deploy-check docs/reports/deploy-check.md

bin=$(venv_bin "$DEMO_DIR")
summary "" "## Kommandoer set fra git-guard" "" \
  "| Kommando | Branch | Sekunder | git-guard committede | Filer |" "|---|---|---|---|---|"
"$bin/python" - "$DEMO_DIR/.git/git-guard.jsonl" >>"$SUMMARY" <<'PY2'
import json, sys
try:
    lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
except FileNotFoundError:
    lines = []
events = [json.loads(line) for line in lines if line.strip()]
for e in events:
    if e.get("event") == "command":
        files = ", ".join(e.get("files", [])) or "-"
        print(f"| /{e['command']} | {e['branch']} | {e['seconds']} | {'ja' if e['committed'] else 'nej'} | {files} |")
other = [e for e in events if e.get("event") not in ("command", "command-start")]
if other:
    print("\nGit-handlinger udført af git-guard:\n")
    for e in other:
        print(f"- {e['ts']}: {e['event']} {e.get('branch') or e.get('from', '')}")
PY2

summary "" "## Kvalitet (kørt af run_all.sh)" "" "| Check | Kommando | Exit | Sidste linje |" "|---|---|---|---|"
quality_check pytest "$bin/python" -m pytest -q -p no:cacheprovider
quality_check ruff "$bin/ruff" check src tests
quality_check import env PYTHONPATH=src "$bin/python" -c "import booking.api"
if command -v docker >/dev/null; then
  quality_check docker-build docker build -q -t "booking-demo-$RUN_ID" .
fi

summary "" "## Git-tjek" ""
bash "$TC_DIR/scripts/check_git.sh" "$DEMO_DIR" "$parent_head" "$parent_branch" | tee "$LOG_DIR/check_git.txt" || true
cat "$LOG_DIR/check_git.txt" >>"$SUMMARY"
summary "" "- Commits efter skabelonen (agenter, git-guard og merges): $(demo_git rev-list --count "$template_head..HEAD")" \
  "- Samlet tid: $(($(date +%s) - started))s"
log "Færdig på $(($(date +%s) - started))s. Opsummering: $SUMMARY"
