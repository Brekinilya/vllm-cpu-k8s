#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

exclude_newer="${EXCLUDE_NEWER:-2026-10-20T00:00:00Z}"
torch_index="https://download.pytorch.org/whl/cpu"

uv pip compile docker/vllm/requirements.in \
  --python-version 3.12 \
  --python-platform x86_64-manylinux_2_39 \
  --torch-backend cpu \
  --emit-index-url \
  --generate-hashes \
  --exclude-newer "$exclude_newer" \
  --custom-compile-command "./scripts/lock.sh" \
  -o docker/vllm/requirements.txt

awk -v index_url="$torch_index" '{ print } /^--index-url / { print "--extra-index-url " index_url }' \
  docker/vllm/requirements.txt > docker/vllm/requirements.txt.tmp
mv docker/vllm/requirements.txt.tmp docker/vllm/requirements.txt

uv pip compile docker/downloader/requirements.in \
  --python-version 3.12 \
  --python-platform x86_64-unknown-linux-musl \
  --generate-hashes \
  --exclude-newer "$exclude_newer" \
  --custom-compile-command "./scripts/lock.sh" \
  -o docker/downloader/requirements.txt
