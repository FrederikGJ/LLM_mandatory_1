#!/usr/bin/env bash
# Opretter et nyt demo-repo ud fra template/ (planens afsnit 1 og 1a):
#   - eget git-repo på branch main, uden remote, med lokal identitet og en pre-push-hook, der altid afviser
#   - egen .venv med app + pytest/ruff/mypy fra de låste requirements-filer (NFR-REP-03, D-02)
# Brug:  scripts/init_demo.sh <demo-mappe>
set -euo pipefail
# shellcheck source=common.sh
source "$(dirname "$0")/common.sh"

DEMO="${1:?brug: scripts/init_demo.sh <demo-mappe>}"
[[ -e "$DEMO" ]] && die "$DEMO findes allerede"
[[ -x "$UV" ]] || die "uv mangler. Kør ./setup.sh først."

mkdir -p "$DEMO"
DEMO="$(cd "$DEMO" && pwd)"
cp -r "$TC_DIR/template/." "$DEMO/"
rm -f "$DEMO"/requirements*.in # kun låsefilerne følger med

git -C "$DEMO" init --quiet --initial-branch=main
# Lokal konfiguration, så globale indstillinger (signering, hooksPath, identitet) ikke påvirker demoen.
git -C "$DEMO" config user.name "frederik_toolchain"
git -C "$DEMO" config user.email "toolchain@localhost"
git -C "$DEMO" config commit.gpgsign false
git -C "$DEMO" config core.hooksPath .git/hooks
git -C "$DEMO" config push.default nothing

cat >"$DEMO/.git/hooks/pre-push" <<'HOOK'
#!/bin/sh
echo "push er spærret i demoen"
exit 1
HOOK
chmod +x "$DEMO/.git/hooks/pre-push"

log "Opretter app-venv i $DEMO/.venv"
"$UV" venv --quiet --seed --python "$PYTHON_VERSION" "$DEMO/.venv"
"$UV" pip install --quiet --python "$DEMO/.venv/bin/python" -r "$DEMO/requirements-dev.txt"

git -C "$DEMO" add -A
git -C "$DEMO" commit --quiet -m "chore: skabelon fra frederik_toolchain"
log "Demo-repo klar: $DEMO ($(git -C "$DEMO" rev-parse --short HEAD))"
