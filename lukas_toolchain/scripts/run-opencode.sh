#!/usr/bin/env bash
# Starter OpenCode med API-nøglerne
# Tjekker først, at begge endpoints svarer.
# Brug:  ./scripts/run-opencode.sh [argumenter til opencode]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/load-keys.sh"

command -v opencode >/dev/null || { echo "FEJL: 'opencode' findes ikke i PATH" >&2; exit 1; }

# Portene skal matche baseURL i opencode.json.
for port in 8081 8082; do
  if ! curl -fsS --max-time 3 "http://127.0.0.1:${port}/health" >/dev/null; then
    echo "FEJL: intet svar fra 127.0.0.1:${port} (kør 'docker compose up -d' i llm_backend)" >&2
    exit 1
  fi
done

# OpenCode skal startes fra mappen med opencode.json.
cd "$ROOT_DIR"
exec opencode "$@"