#!/bin/sh
# Downloader GGUF-modellerne til MODELS_DIR, hvis de ikke allerede findes.
# Køres automatisk af compose-servicen `model-init`, men kan også køres direkte:
#   ./scripts/download-models.sh
# POSIX sh, så det også virker i curlimages/curl (busybox).
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT_DIR=$(dirname "$SCRIPT_DIR")

# Kørt direkte på host: hent variabler fra .env.
if [ -z "${MODEL_A_FILE:-}" ] && [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  set +a
fi

MODELS_DIR=${MODELS_DIR:-$ROOT_DIR/models}

for var in MODEL_A_FILE MODEL_A_URL MODEL_B_FILE MODEL_B_URL; do
  eval "val=\${$var:-}"
  if [ -z "$val" ]; then
    echo "FEJL: $var er ikke sat (se .env.example)" >&2
    exit 1
  fi
done

mkdir -p "$MODELS_DIR"
if [ ! -w "$MODELS_DIR" ]; then
  echo "FEJL: $MODELS_DIR er ikke skrivbar for uid $(id -u). Tjek HOST_UID/HOST_GID i .env." >&2
  exit 1
fi

# GGUF-filer starter med de fire bytes "GGUF" (fanger fx HTML-fejlsider).
is_gguf() {
  [ "$(head -c 4 "$1" 2>/dev/null)" = "GGUF" ]
}

download() {
  label=$1
  file=$2
  url=$3
  target="$MODELS_DIR/$file"
  tmp="$target.part"

  if [ -s "$target" ] && is_gguf "$target"; then
    echo "[$label] findes allerede: $file"
    return 0
  fi

  echo "[$label] henter $file"
  echo "        fra $url"
  set -- -fL --retry 5 --retry-delay 5 -C - --progress-bar -o "$tmp"
  if [ -n "${HF_TOKEN:-}" ]; then
    set -- "$@" -H "Authorization: Bearer $HF_TOKEN"
  fi
  curl "$@" "$url"

  if ! is_gguf "$tmp"; then
    echo "FEJL: [$label] den hentede fil er ikke en gyldig GGUF-fil" >&2
    rm -f "$tmp"
    exit 1
  fi
  mv "$tmp" "$target"
  echo "[$label] færdig: $(du -h "$target" | cut -f1)"
}

download "llm-a" "$MODEL_A_FILE" "$MODEL_A_URL"
download "llm-b" "$MODEL_B_FILE" "$MODEL_B_URL"
echo "Alle modeller er klar i $MODELS_DIR"
