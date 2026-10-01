#!/usr/bin/env bash
# Hele pipelinen i fast rækkefølge (NFR-REP-03):
#   architect -> tech_lead -> coder_1 + coder_2 (hver sin branch, merges af scriptet)
#   -> tester (tests) -> kvalitetsgate [-> tech_lead fix] -> tester (rapport) -> deploy -> docs -> git-tjek
#
# Brug:  ./run_all.sh                 ny kørsel i runs/run-<tidspunkt>/
#        RUN_ID=run-a ./run_all.sh    eget navn på kørslen
#        FIX_ROUNDS=0 ./run_all.sh    ingen rettelsesrunde, hvis kvalitetsgaten fejler (standard: 1)
set -euo pipefail
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/scripts/common.sh"
require_venv

RUN_ID="${RUN_ID:-run-$(date +%Y%m%d-%H%M%S)}"
RUN_DIR="$RUNS_DIR/$RUN_ID"
FIX_ROUNDS="${FIX_ROUNDS:-1}"
export DEMO_DIR="$RUN_DIR/demo" LOG_DIR="$RUN_DIR/logs"

# Exit 0 = ok, 2 = opgaven gav ikke den forventede fil (pipelinen fortsætter), andet = stop.
role() {
  local rc=0
  "$TC_DIR/run_role.sh" "$@" || rc=$?
  [[ $rc -eq 0 || $rc -eq 2 ]] || die "run_role.sh $* stoppede med exit $rc"
}

demo_git() {
  git -C "$DEMO_DIR" "$@"
}

"$VENV/bin/python" "$TC_DIR/scripts/toolchain.py" check || die "backend-tjek fejlede (se ovenfor)"
parent_head=$(git -C "$REPO_DIR" rev-parse HEAD)
started=$(date +%s)

mkdir -p "$LOG_DIR"
"$TC_DIR/scripts/init_demo.sh" "$DEMO_DIR"
ln -sfn "$RUN_ID" "$RUNS_DIR/latest"

role architect
role tech_lead

# FR-IMP-01: to coder-instanser på hver sin branch fra samme commit. Kun scriptet opretter branches og
# merger (afsnit 1a). llm-b kører med --parallel 1, så instanserne kører efter hinanden.
demo_git branch coder_1
demo_git branch coder_2
demo_git switch --quiet coder_1
role coder_1
demo_git switch --quiet coder_2
role coder_2
demo_git switch --quiet main
for branch in coder_1 coder_2; do
  if ! demo_git merge --no-ff --quiet -m "merge: $branch" "$branch"; then
    demo_git merge --abort
    die "merge af $branch gav konflikt; se $DEMO_DIR"
  fi
  log "Merget $branch ind i main"
done

role tester tests
round=0
until "$TC_DIR/scripts/quality.sh" "$DEMO_DIR" >/dev/null; do
  ((round < FIX_ROUNDS)) || break
  round=$((round + 1))
  log "Kvalitetsgaten fejlede; rettelsesrunde $round af $FIX_ROUNDS"
  role tech_lead fix
done
role tester report # kører quality.sh igen først (run_before i config/roles.yaml)
role deploy
role docs

"$TC_DIR/scripts/check_git.sh" "$DEMO_DIR" "$parent_head" | tee "$LOG_DIR/check_git.txt" || true
"$VENV/bin/python" "$TC_DIR/scripts/toolchain.py" summary >/dev/null
log "Færdig på $(($(date +%s) - started))s. Opsummering: $RUN_DIR/summary.md"
