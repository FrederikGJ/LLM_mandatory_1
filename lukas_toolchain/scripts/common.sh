# Fælles stier og hjælpefunktioner. Sources af de andre scripts, køres ikke selv.
# shellcheck shell=bash

TC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$TC_DIR/.." && pwd)" # llm_mandatory_1
RUNS_DIR="${RUNS_DIR:-$TC_DIR/runs}"

# Python til demoens .venv. Overskriv fx med PYTHON="py -3.12" på Windows.
read -ra PYTHON_CMD <<<"${PYTHON:-python3}"

die() {
  echo "FEJL: $*" >&2
  exit 1
}

log() {
  printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"
}

# .venv/Scripts på Windows, .venv/bin ellers.
venv_bin() {
  if [[ -d "$1/.venv/Scripts" ]]; then echo "$1/.venv/Scripts"; else echo "$1/.venv/bin"; fi
}

# git-roden som bash-sti (git giver C:/... på Windows, pwd giver /c/...).
git_root() {
  (cd "$(git -C "$1" rev-parse --show-toplevel)" && pwd)
}

# Agenterne må kun køre i et demo-repo, der er sin egen git-rod, ikke har nogen remote og har pre-push-hooken.
require_demo_repo() {
  local demo=$1
  [[ -d "$demo/.git" ]] || die "$demo er ikke et demo-repo (kør scripts/init_demo.sh)"
  [[ "$demo" != "$REPO_DIR" ]] || die "demo-repoet må ikke være llm_mandatory_1"
  [[ "$(git_root "$demo")" == "$demo" ]] || die "$demo er ikke roden af sit eget git-repo"
  [[ -z "$(git -C "$demo" remote)" ]] || die "demo-repoet har en remote ($(git -C "$demo" remote))"
  [[ -f "$demo/.git/hooks/pre-push" ]] || die "pre-push-hook mangler i $demo"
}
