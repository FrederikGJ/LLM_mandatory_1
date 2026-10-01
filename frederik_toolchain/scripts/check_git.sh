#!/usr/bin/env bash
# Tjekker git-politikken efter en kørsel (planens afsnit 1a).
# Brug:  scripts/check_git.sh <demo-mappe> [forventet HEAD i llm_mandatory_1]
set -uo pipefail
# shellcheck source=common.sh
source "$(dirname "$0")/common.sh"
DEMO="${1:?brug: scripts/check_git.sh <demo-mappe> [parent-head]}"
EXPECTED_PARENT_HEAD="${2:-}"
ALLOWED_BRANCHES="coder_1 coder_2 main"
failures=0

check() { # <ok?> <tekst>
  if [[ "$1" == 0 ]]; then echo "- [ok] $2"; else echo "- [FEJL] $2"; failures=$((failures + 1)); fi
}

remotes=$(git -C "$DEMO" remote -v)
check "$([[ -z "$remotes" ]] && echo 0 || echo 1)" "ingen remote i demo-repoet${remotes:+: $remotes}"

branches=$(git -C "$DEMO" branch --format='%(refname:short)' | sort | tr '\n' ' ' | sed 's/ $//')
unexpected=$(comm -23 <(tr ' ' '\n' <<<"$branches" | sort) <(tr ' ' '\n' <<<"$ALLOWED_BRANCHES" | sort) | tr '\n' ' ')
check "$([[ -z "${unexpected// /}" ]] && echo 0 || echo 1)" "kun forventede branches: $branches"

hook="$DEMO/.git/hooks/pre-push"
check "$([[ -x "$hook" ]] && grep -q 'exit 1' "$hook" && echo 0 || echo 1)" "pre-push-hook blokerer push"
check "$([[ "$(git -C "$DEMO" config core.hooksPath)" == ".git/hooks" ]] && echo 0 || echo 1)" "core.hooksPath peger på .git/hooks"

current=$(git -C "$DEMO" branch --show-current)
check "$([[ "$current" == "main" ]] && echo 0 || echo 1)" "demo-repoet står på main (står på: $current)"

dirty=$(git -C "$DEMO" status --porcelain)
check "$([[ -z "$dirty" ]] && echo 0 || echo 1)" "ingen ucommittede ændringer i demo-repoet"

if [[ -n "$EXPECTED_PARENT_HEAD" ]]; then
  parent_head=$(git -C "$REPO_DIR" rev-parse HEAD)
  check "$([[ "$parent_head" == "$EXPECTED_PARENT_HEAD" ]] && echo 0 || echo 1)" \
    "llm_mandatory_1 har samme HEAD som før kørslen (${parent_head:0:7})"
fi

echo
echo "Git-tjek: $([[ $failures -eq 0 ]] && echo "alt ok" || echo "$failures fejl")"
exit $((failures > 0))
