#!/usr/bin/env bash
# Sammenligner strukturen i to kørsler (NFR-REP-03): filer, linjetal, commits og kvalitet.
# Brug:  scripts/compare_runs.sh <run-a> <run-b>     (mapper under runs/, fx runs/run-20261001-120000)
set -uo pipefail
A="${1:?brug: scripts/compare_runs.sh <run-a> <run-b>}"
B="${2:?brug: scripts/compare_runs.sh <run-a> <run-b>}"

files() { git -C "$1/demo" ls-files | sort; }
lines() { (cd "$1/demo" && git ls-files | sort | while read -r f; do printf '%s\t%s\n' "$f" "$(wc -l <"$f")"; done); }

echo "# Sammenligning: $(basename "$A") vs $(basename "$B")"
echo
echo "## Filstruktur"
if diff <(files "$A") <(files "$B") >/dev/null; then
  echo "Identisk: $(files "$A" | wc -l) filer i begge kørsler."
else
  echo "Forskelle (< kun i $(basename "$A"), > kun i $(basename "$B")):"
  echo
  diff <(files "$A") <(files "$B") | grep '^[<>]' | sed 's/^/    /'
fi
echo
echo "## Linjetal pr. fil"
echo
echo "| Fil | $(basename "$A") | $(basename "$B") |"
echo "|---|---|---|"
join -t $'\t' -a1 -a2 -e '-' -o 0,1.2,2.2 <(lines "$A") <(lines "$B") | awk -F'\t' '{printf "| %s | %s | %s |\n", $1, $2, $3}'
echo
echo "## Commits pr. branch"
echo
for run in "$A" "$B"; do
  printf -- '- %s: ' "$(basename "$run")"
  for b in $(git -C "$run/demo" branch --format='%(refname:short)'); do
    printf '%s=%s ' "$b" "$(git -C "$run/demo" rev-list --count "$b")"
  done
  echo
done
echo
echo "## Identiske filer (samme indhold)"
same=0
total=0
while read -r f; do
  total=$((total + 1))
  cmp -s "$A/demo/$f" "$B/demo/$f" && same=$((same + 1))
done < <(comm -12 <(files "$A") <(files "$B"))
echo
echo "$same af $total fælles filer er byte-identiske."
echo
echo "## Kvalitet"
for run in "$A" "$B"; do
  echo
  echo "### $(basename "$run")"
  echo
  grep '^|' "$run/demo/reports/raw/summary.md" 2>/dev/null || echo "(ingen reports/raw/summary.md)"
done
