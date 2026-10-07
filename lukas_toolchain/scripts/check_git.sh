#!/usr/bin/env bash
# Tjekker git-politikken efter en kørsel.
# Brug:  bash scripts/check_git.sh <demo-mappe> [forventet HEAD i llm_mandatory_1] [forventet branch i llm_mandatory_1]
set -uo pipefail
# shellcheck source=common.sh
source "$(dirname "$0")/common.sh"
DEMO="$(cd "${1:?brug: scripts/check_git.sh <demo-mappe> [parent-head] [parent-branch]}" && pwd)"
EXPECTED_PARENT_HEAD="${2:-}"
EXPECTED_PARENT_BRANCH="${3:-}"
ALLOWED_BRANCHES="coder_1 coder_2 main"
failures=0

check() { # <ok?> <tekst>
  if [[ "$1" == 0 ]]; then echo "- [ok] $2"; else echo "- [FEJL] $2"; failures=$((failures + 1)); fi
}

check "$([[ "$(git_root "$DEMO")" == "$DEMO" ]] && echo 0 || echo 1)" "demo-repoet er sin egen git-rod"

remotes=$(git -C "$DEMO" remote -v)
check "$([[ -z "$remotes" ]] && echo 0 || echo 1)" "ingen remote i demo-repoet${remotes:+: $remotes}"

branches=$(git -C "$DEMO" branch --format='%(refname:short)' | sort | tr '\n' ' ' | sed 's/ $//')
unexpected=$(comm -23 <(tr ' ' '\n' <<<"$branches" | sort) <(tr ' ' '\n' <<<"$ALLOWED_BRANCHES" | sort) | tr '\n' ' ')
check "$([[ -z "${unexpected// /}" ]] && echo 0 || echo 1)" "kun forventede branches: $branches"

tags=$(git -C "$DEMO" tag)
check "$([[ -z "$tags" ]] && echo 0 || echo 1)" "ingen tags${tags:+: $tags}"

hook="$DEMO/.git/hooks/pre-push"
check "$([[ -f "$hook" ]] && grep -q 'exit 1' "$hook" && echo 0 || echo 1)" "pre-push-hook blokerer push"
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
if [[ -n "$EXPECTED_PARENT_BRANCH" ]]; then
  parent_branch=$(git -C "$REPO_DIR" branch --show-current)
  check "$([[ "$parent_branch" == "$EXPECTED_PARENT_BRANCH" ]] && echo 0 || echo 1)" \
    "llm_mandatory_1 står stadig på $EXPECTED_PARENT_BRANCH (står på: $parent_branch)"
fi

echo
echo "Git-tjek: $([[ $failures -eq 0 ]] && echo "alt ok" || echo "$failures fejl")"
exit $((failures > 0))
