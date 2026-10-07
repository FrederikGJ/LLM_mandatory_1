#!/usr/bin/env bash
# Viser model-id'erne på begge endpoints. De skal matche nøglerne under
# "models" i opencode.json (fx qwen2.5-coder-3b og qwen2.5-coder-1.5b).
# Brug:  ./scripts/check-models.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/load-keys.sh"

failures=0

check() {
  local name=$1 port=$2 key=$3
  local body http_code

  body=$(mktemp)
  http_code=$(curl -s -o "$body" -w '%{http_code}' --max-time 5 \
    -H "Authorization: Bearer ${key}" \
    "http://127.0.0.1:${port}/v1/models") || http_code=000

  printf '%-6s (127.0.0.1:%s): ' "$name" "$port"
  case "$http_code" in
    200)
      python3 -c 'import json, sys; print(", ".join(m["id"] for m in json.load(open(sys.argv[1]))["data"]))' "$body"
      ;;
    503)
      echo "FEJL: modellen indlæses stadig (503). Vent og prøv igen."
      failures=$((failures + 1))
      ;;
    401)
      echo "FEJL: forkert API-nøgle (401). Tjek llm_backend/.env."
      failures=$((failures + 1))
      ;;
    000)
      echo "FEJL: intet svar. Kører containeren? (docker compose ps i llm_backend)"
      failures=$((failures + 1))
      ;;
    *)
      echo "FEJL: HTTP ${http_code}: $(head -c 200 "$body")"
      failures=$((failures + 1))
      ;;
  esac
  rm -f "$body"
}

check llm-a 8081 "$LLM_A_API_KEY"
check llm-b 8082 "$LLM_B_API_KEY"

if (( failures > 0 )); then
  echo "RESULTAT: ${failures} endpoint(s) fejlede"
  exit 1
fi
echo "Alt er i orden"

exit $(( failures > 0 ))