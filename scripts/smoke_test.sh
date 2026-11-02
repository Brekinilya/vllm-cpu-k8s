#!/usr/bin/env bash
set -euo pipefail

namespace="${NAMESPACE:-llm-inference}"
model="${MODEL:-qwen2.5-1.5b-instruct}"
local_port="${LOCAL_PORT:-18000}"
base_url="${BASE_URL:-}"

pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; exit 1; }

if [[ -z "$base_url" ]]; then
  kubectl -n "$namespace" port-forward svc/vllm "$local_port:8000" >/dev/null 2>&1 &
  forward_pid=$!
  trap 'kill $forward_pid 2>/dev/null || true' EXIT
  base_url="http://127.0.0.1:$local_port"
  for _ in $(seq 1 30); do
    curl -s -o /dev/null "$base_url/health" && break
    sleep 1
  done
fi

api_key="${VLLM_API_KEY:-$(kubectl -n "$namespace" get secret vllm-secrets -o jsonpath='{.data.API_KEY}' | base64 -d)}"
canary="canary-$(date +%s)-$RANDOM"

[[ "$(curl -s -o /dev/null -w '%{http_code}' "$base_url/health")" == 200 ]] \
  && pass "/health is open for probes" || fail "/health is not reachable"

[[ "$(curl -s -o /dev/null -w '%{http_code}' "$base_url/v1/models")" == 401 ]] \
  && pass "request without a token is rejected with 401" || fail "request without a token was not rejected"

[[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer wrong-token' "$base_url/v1/models")" == 401 ]] \
  && pass "request with a wrong token is rejected with 401" || fail "request with a wrong token was not rejected"

curl -sf -H "Authorization: Bearer $api_key" "$base_url/v1/models" | grep -q "\"$model\"" \
  && pass "model $model is served" || fail "model $model is not listed"

payload=$(printf '{"model":"%s","messages":[{"role":"user","content":"Repeat exactly: %s"}],"max_tokens":32,"temperature":0}' "$model" "$canary")
response=$(curl -sf "$base_url/v1/chat/completions" -H "Authorization: Bearer $api_key" -H 'Content-Type: application/json' -d "$payload")
grep -q "$canary" <<<"$response" \
  && pass "completion works and the answer contains the marker" || fail "unexpected completion: $response"

sleep 2
logs=$(kubectl -n "$namespace" logs deploy/vllm -c vllm --since=10m)

if grep -q "$canary" <<<"$logs"; then
  fail "marker from the prompt or the answer leaked into the logs"
fi
pass "neither the prompt nor the answer is present in the logs"

grep -q "POST /v1/chat/completions status=200" <<<"$logs" \
  && pass "the request itself is logged by the access middleware" || fail "access log line is missing"

grep -q "Received request chatcmpl-" <<<"$logs" \
  && pass "the engine logs the request id without content" || fail "engine request log line is missing"

echo
echo "last log lines:"
grep -E "vllm_privacy.access|request_logger" <<<"$logs" | tail -4
