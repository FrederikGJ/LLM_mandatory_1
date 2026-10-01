#!/usr/bin/env bash
# Kør én rolle (alle dens opgaver, eller de nævnte) med rollens prompt og endpoint.
#
# Brug:  ./run_role.sh <rolle> [opgave ...] [--interactive]
#        ./run_role.sh --check          tjek endpoints, nøgler og rollebinding
#
# Demo-repoet er $DEMO_DIR (standard: runs/latest/demo). Roller og opgaver: config/roles.yaml.
# Endpoint pr. rolle: llm_backend/config/endpoints.yaml.
set -euo pipefail
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/scripts/common.sh"
require_venv

if [[ "${1:-}" == "--check" ]]; then
  exec "$VENV/bin/python" "$TC_DIR/scripts/toolchain.py" check
fi
[[ $# -ge 1 ]] || die "brug: ./run_role.sh <rolle> [opgave ...] [--interactive]"
exec "$VENV/bin/python" "$TC_DIR/scripts/toolchain.py" run "$@"
