#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

label="${1:?usage: bench/run.sh <label>}"
namespace="${NAMESPACE:-llm-inference}"
results="bench/results"

kubectl -n "$namespace" rollout status deploy/vllm --timeout=30m >/dev/null
kubectl -n "$namespace" delete job vllm-bench --ignore-not-found --wait=true >/dev/null
kubectl apply -k bench >/dev/null
echo "bench job started: $label"

until status="$(kubectl -n "$namespace" get job vllm-bench -o jsonpath='{.status.succeeded}/{.status.failed}')" \
  && [[ "$status" == 1/* || "$status" == */1 ]]; do
  sleep 10
done

mkdir -p "$results"
kubectl -n "$namespace" logs job/vllm-bench > "$results/$label.log"
grep '^JSON ' "$results/$label.log" | sed 's/^JSON //' > "$results/$label.json"
grep '^|' "$results/$label.log" > "$results/$label.md"

cat "$results/$label.md"
[[ "$status" == 1/* ]]
