#!/usr/bin/env bash
# Åbner OpenCode's TUI i et demo-repo (aldrig i llm_mandatory_1). Kør derefter /plan, /implement og /finish
# og gennemgå diffen mellem hver kommando.
# Brug:  bash scripts/run-opencode.sh [demo-mappe]     standard: demoen fra seneste run_all.sh / init_demo.sh
#        Nyt demo-repo først:  bash scripts/init_demo.sh runs/<navn>/demo
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/load-keys.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_opencode.sh"

command -v opencode >/dev/null || { echo "FEJL: 'opencode' findes ikke i PATH" >&2; exit 1; }

DEMO="${1:-}"
if [[ -z "$DEMO" ]]; then
  [[ -f "$RUNS_DIR/LATEST" ]] || die "intet demo-repo endnu. Kør: bash scripts/init_demo.sh runs/<navn>/demo"
  DEMO="$RUNS_DIR/$(cat "$RUNS_DIR/LATEST")/demo"
fi
DEMO="$(cd "$DEMO" && pwd)"
require_demo_repo "$DEMO"

# Portene skal matche baseURL i opencode.json.
for port in 8081 8082; do
  if ! curl -fsS --max-time 3 "http://127.0.0.1:${port}/health" >/dev/null; then
    echo "FEJL: intet svar fra 127.0.0.1:${port} (kør 'docker compose up -d' i llm_backend)" >&2
    exit 1
  fi
done

log "Åbner OpenCode i $DEMO"
opencode_in_demo "$DEMO" "$@"
