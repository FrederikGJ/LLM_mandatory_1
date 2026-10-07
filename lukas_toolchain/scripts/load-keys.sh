# Indlæses med 'source' af de andre scripts, køres ikke direkte.
# Eksporterer LLM_A_API_KEY og LLM_B_API_KEY,
# så nøglerne aldrig skal stå i opencode.json eller i git.

_keys_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_env_file="$_keys_dir/../../llm_backend/.env"

if [[ ! -f "$_env_file" ]]; then
  echo "FEJL: $_env_file findes ikke (kopiér .env.example til .env i llm_backend)" >&2
  return 1
fi

# Læs kun de to nøgler. '\r' fjernes, hvis .env har Windows-linjeskift.
while IFS='=' read -r _k _v || [[ -n "$_k" ]]; do
  _v="${_v%$'\r'}"
  case "$_k" in
    LLM_A_API_KEY|LLM_B_API_KEY) export "$_k=$_v" ;;
  esac
done < "$_env_file"

for _var in LLM_A_API_KEY LLM_B_API_KEY; do
  if [[ -z "${!_var:-}" ]]; then
    echo "FEJL: $_var mangler i $_env_file" >&2
    return 1
  fi
done
unset _keys_dir _env_file _k _v _var