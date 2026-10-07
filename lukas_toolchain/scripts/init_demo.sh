#!/usr/bin/env bash
# Opretter et nyt demo-repo ud fra template/ (kopieret fra frederik_toolchain):
#   - eget git-repo på branch main, uden remote, med lokal identitet og en pre-push-hook, der altid afviser
#   - egen .venv med app + pytest/ruff/mypy fra requirements-dev.txt
# Brug:  bash scripts/init_demo.sh <demo-mappe>
set -euo pipefail
# shellcheck source=common.sh
source "$(dirname "$0")/common.sh"

DEMO="${1:?brug: scripts/init_demo.sh <demo-mappe>}"
[[ -e "$DEMO" ]] && die "$DEMO findes allerede"

mkdir -p "$DEMO"
DEMO="$(cd "$DEMO" && pwd)"
cp -r "$TC_DIR/template/." "$DEMO/"

git -C "$DEMO" init --quiet --initial-branch=main
# Lokal konfiguration, så globale indstillinger (signering, hooksPath, identitet) ikke påvirker demoen.
git -C "$DEMO" config user.name "lukas_toolchain"
git -C "$DEMO" config user.email "toolchain@localhost"
git -C "$DEMO" config commit.gpgsign false
git -C "$DEMO" config core.hooksPath .git/hooks
git -C "$DEMO" config push.default nothing
# Ingen LF->CRLF-konvertering (giver advarsler på Windows og støj i diffs).
git -C "$DEMO" config core.autocrlf false

cat >"$DEMO/.git/hooks/pre-push" <<'HOOK'
#!/bin/sh
echo "push er spærret i demoen"
exit 1
HOOK
chmod +x "$DEMO/.git/hooks/pre-push"

log "Opretter app-venv i $DEMO/.venv"
"${PYTHON_CMD[@]}" -m venv "$DEMO/.venv"
"$(venv_bin "$DEMO")/python" -m pip install --quiet -r "$DEMO/requirements-dev.txt"

git -C "$DEMO" add -A
git -C "$DEMO" commit --quiet -m "chore: skabelon fra lukas_toolchain"
# Seneste demo, så scripts/run-opencode.sh kan finde den (stien relativt til runs/).
if [[ "$DEMO" == "$RUNS_DIR"/*/demo ]]; then
  rel="${DEMO#"$RUNS_DIR"/}"
  echo "${rel%/demo}" >"$RUNS_DIR/LATEST"
fi
log "Demo-repo klar: $DEMO ($(git -C "$DEMO" rev-parse --short HEAD))"
