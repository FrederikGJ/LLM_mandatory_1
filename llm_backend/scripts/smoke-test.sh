#!/usr/bin/env bash
# Smoke-test af begge endpoints:
#   1. /health svarer
#   2. en request UDEN API-nøgle afvises med 401 (NFR-SEC-01)
#   3. en chat-request MED nøgle lykkes; viser svartid og tokens/sek.
# Brug:  ./scripts/smoke-test.sh ["valgfri prompt"]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

for cmd in curl python3; do
  command -v "$cmd" >/dev/null || { echo "FEJL: '$cmd' mangler" >&2; exit 1; }
done

PROMPT="${1:-Skriv en kort Python-funktion, der returnerer de første n Fibonacci-tal.}"
MAX_TOKENS="${SMOKE_MAX_TOKENS:-256}"
failures=0

test_endpoint() {
  local name=$1 port=$2 key=$3
  local url="http://127.0.0.1:${port}"
  echo "=== ${name} (${url}) ==="

  if [[ -z "$key" ]]; then
    echo "  FEJL: API-nøgle mangler i .env"
    return 1
  fi

  if ! curl -fsS --max-time 5 "${url}/health" >/dev/null; then
    echo "  FEJL: /health svarer ikke (kører containeren? se 'docker compose ps')"
    return 1
  fi
  echo "  health:        ok"

  local unauth
  unauth=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    -H 'Content-Type: application/json' \
    -d '{"messages":[{"role":"user","content":"hi"}],"max_tokens":1}' \
    "${url}/v1/chat/completions")
  if [[ "$unauth" != "401" ]]; then
    echo "  FEJL: request uden nøgle gav HTTP ${unauth}, forventede 401"
    return 1
  fi
  echo "  uden nøgle:    afvist (401)"

  local body resp out http_code time_total
  body=$(python3 -c 'import json, sys
print(json.dumps({
    "messages": [{"role": "user", "content": sys.argv[1]}],
    "max_tokens": int(sys.argv[2]),
    "temperature": 0.2,
}))' "$PROMPT" "$MAX_TOKENS")

  resp=$(mktemp)
  out=$(curl -sS -o "$resp" -w '%{http_code} %{time_total}' --max-time 600 \
    -H "Authorization: Bearer ${key}" \
    -H 'Content-Type: application/json' \
    -d "$body" \
    "${url}/v1/chat/completions") || { echo "  FEJL: request fejlede"; rm -f "$resp"; return 1; }
  read -r http_code time_total <<<"$out"

  if [[ "$http_code" != "200" ]]; then
    echo "  FEJL: HTTP ${http_code}: $(head -c 300 "$resp")"
    rm -f "$resp"
    return 1
  fi

  python3 - "$resp" "$time_total" <<'PY'
import json, sys

r = json.load(open(sys.argv[1]))
total = float(sys.argv[2])
usage = r.get("usage", {})
timings = r.get("timings", {})
completion = usage.get("completion_tokens", 0)
gen_tps = timings.get("predicted_per_second") or (completion / total if total else 0)
prompt_tps = timings.get("prompt_per_second")
answer = r["choices"][0]["message"]["content"].strip().replace("\n", " ")

print(f"  model:         {r.get('model', '?')}")
print(f"  svartid:       {total:.2f} s")
print(f"  tokens:        prompt={usage.get('prompt_tokens', '?')} svar={completion}")
print(f"  generering:    {gen_tps:.1f} tokens/s")
if prompt_tps:
    print(f"  prompt-eval:   {prompt_tps:.1f} tokens/s")
print(f"  svar (uddrag): {answer[:160]}{'...' if len(answer) > 160 else ''}")
PY
  rm -f "$resp"
}

test_endpoint "llm-a" "${LLM_A_PORT:-8081}" "${LLM_A_API_KEY:-}" || failures=$((failures + 1))
echo
test_endpoint "llm-b" "${LLM_B_PORT:-8082}" "${LLM_B_API_KEY:-}" || failures=$((failures + 1))
echo

if (( failures > 0 )); then
  echo "RESULTAT: ${failures} endpoint(s) fejlede"
  exit 1
fi
echo "RESULTAT: begge endpoints ok"
