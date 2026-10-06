# Starter OpenCode i et demo-repo med lukas_toolchains config, agenter, commands og plugin.
# Sources af run_all.sh og run-opencode.sh. Kræver common.sh og load-keys.sh først.
# shellcheck shell=bash

# OpenCode leder kun efter opencode.json op til nærmeste git-rod (= demo-repoet),
# så config og .opencode peges ud til lukas_toolchain med miljøvariabler.
# Demoens .venv står først i PATH, så agenternes pytest/ruff er demoens egne.
opencode_in_demo() {
  local demo=$1
  shift
  (
    cd "$demo"
    PATH="$(venv_bin "$demo"):$PATH" \
      OPENCODE_CONFIG="$TC_DIR/opencode.json" \
      OPENCODE_CONFIG_DIR="$TC_DIR/.opencode" \
      opencode "$@"
  )
}
