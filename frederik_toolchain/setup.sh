#!/usr/bin/env bash
# Opsætning af toolchainen (D-02):
#   1. uv (låst version) i .uv/venv, bygget med systemets python3
#   2. Python 3.12 hentet af uv til .uv/python (Aider understøtter ikke 3.13+)
#   3. .venv med Aider fra den låste requirements.txt
#
# Brug:  ./setup.sh          installér/opdatér .venv ud fra låsefilerne
#        ./setup.sh --lock   generér låsefilerne igen (toolchain + demo-skabelon)
set -euo pipefail
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/scripts/common.sh"

command -v python3 >/dev/null || die "python3 mangler (bruges kun til at bootstrappe uv)"

if [[ ! -x "$UV" ]] || [[ "$("$UV" --version | cut -d' ' -f2)" != "$UV_VERSION" ]]; then
  log "Installerer uv $UV_VERSION i .uv/venv"
  rm -rf "$TC_DIR/.uv/venv"
  python3 -m venv "$TC_DIR/.uv/venv"
  "$TC_DIR/.uv/venv/bin/pip" install --quiet --disable-pip-version-check "uv==$UV_VERSION"
fi

if [[ "${1:-}" == "--lock" ]]; then
  compile() { # <in> <out> [ekstra args]
    local src=$1 out=$2
    shift 2
    log "Låser $out"
    "$UV" pip compile --quiet --universal --python-version 3.12 \
      --no-header --annotation-style line "$@" "$src" -o "$out"
  }
  (cd "$TC_DIR" && compile requirements.in requirements.txt)
  (cd "$TC_DIR/template" && compile requirements.in requirements.txt)
  # Dev-låsen holdes på samme runtime-versioner som requirements.txt.
  (cd "$TC_DIR/template" && compile requirements-dev.in requirements-dev.txt -c requirements.txt)
  exit 0
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  log "Opretter .venv med Python $PYTHON_VERSION"
  "$UV" venv --quiet --seed --python "$PYTHON_VERSION" "$VENV"
fi

log "Installerer Aider fra requirements.txt"
"$UV" pip install --quiet --python "$VENV/bin/python" -r "$TC_DIR/requirements.txt"

log "Klar: $("$VENV/bin/aider" --version) på $("$VENV/bin/python" --version)"
echo "Næste skridt: start llm_backend (se ../llm_backend/README.md) og kør ./run_role.sh --check"
