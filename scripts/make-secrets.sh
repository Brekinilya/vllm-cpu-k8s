#!/usr/bin/env bash
set -euo pipefail

target="${1:-deploy/overlays/minikube/secrets.env}"

if [[ -f "$target" ]]; then
  echo "$target already exists, leaving it untouched"
  exit 0
fi

api_key="$(openssl rand -hex 32)"

umask 077
printf 'API_KEY=%s\nHF_TOKEN=%s\n' "$api_key" "${HF_TOKEN:-}" > "$target"
echo "wrote $target"
