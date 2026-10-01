# Fælles stier og miljø for toolchain-scripts. Sources af de andre scripts, køres ikke selv.
# shellcheck shell=bash

TC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$TC_DIR/.." && pwd)" # llm_mandatory_1

# Låste versioner (NFR-REP-03). uv og Python ligger i .uv/, så intet installeres globalt.
UV_VERSION=0.12.21
PYTHON_VERSION=3.12.14

UV="$TC_DIR/.uv/venv/bin/uv"
VENV="$TC_DIR/.venv"
export UV_PYTHON_INSTALL_DIR="$TC_DIR/.uv/python"
export UV_CACHE_DIR="$TC_DIR/.uv/cache"
export UV_PYTHON_PREFERENCE=only-managed

RUNS_DIR="${RUNS_DIR:-$TC_DIR/runs}"

die() {
  echo "FEJL: $*" >&2
  exit 1
}

log() {
  printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"
}

require_venv() {
  [[ -x "$VENV/bin/aider" ]] || die "Aider er ikke installeret. Kør ./setup.sh først."
}
