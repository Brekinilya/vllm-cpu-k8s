import argparse
import http.client
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

WORDS = (
    "system cluster memory request token model latency network storage policy "
    "kernel thread process buffer engine scheduler batch window context vector "
    "matrix tensor weight layer signal output input stream packet router switch "
    "server client socket header payload record table index query result cache "
    "volume driver module service config secret runtime image registry shell "
    "garden river mountain forest ocean desert island valley meadow harbor bridge "
    "castle market village museum library school office station airport highway"
).split()

WARMUP_PROMPT_TOKENS = 64


@dataclass
class Scenario:
    name: str
    prompt_tokens: int
    concurrency: list[int]


@dataclass
class RequestResult:
    ok: bool
    ttft: float = 0.0
    latency: float = 0.0
    prompt_tokens: int = 0
    output_tokens: int = 0
    inter_token: list[float] = field(default_factory=list)
    error: str = ""


@dataclass
class ScenarioReport:
    scenario: str
    concurrency: int
    requests: int
    errors: int
    duration_s: float
    mean_prompt_tokens: float
    mean_output_tokens: float
    ttft_p50_ms: float
    ttft_p95_ms: float
    latency_p50_s: float
    latency_p95_s: float
    tpot_p50_ms: float
    itl_p95_ms: float
    request_throughput: float
    output_throughput: float
    total_throughput: float


def parse_plan(plan: str) -> list[Scenario]:
    scenarios = []
    for item in plan.split():
        name, spec = item.split("=", 1)
        tokens, levels = spec.split("@", 1)
        scenarios.append(Scenario(name, int(tokens), [int(level) for level in levels.split(",")]))
    return scenarios


def build_prompt(target_tokens: int, rng: random.Random) -> str:
    words = [rng.choice(WORDS) for _ in range(target_tokens)]
    return "Continue this list of words with more words: " + " ".join(words)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


class Client:
    def __init__(self, base_url: str, api_key: str, max_tokens: int, timeout: float):
        parsed = urlparse(base_url)
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        self.prefix = parsed.path.rstrip("/")
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.model = ""

    def discover_model(self) -> str:
        connection = self.connection_class(self.host, self.port, timeout=30)
        try:
            connection.request("GET", self.prefix + "/v1/models", headers=self.headers)
            response = connection.getresponse()
            payload = json.loads(response.read())
        finally:
            connection.close()
        self.model = payload["data"][0]["id"]
        return self.model

    def send(self, prompt: str, max_tokens: int | None = None) -> RequestResult:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": 0.0,
            "ignore_eos": True,
            "stream": True,
            "stream_options": {"include_usage": True},
        })
        connection = self.connection_class(self.host, self.port, timeout=self.timeout)
        started = time.perf_counter()
        result = RequestResult(ok=False)
        try:
            connection.request("POST", self.prefix + "/v1/chat/completions", body=body, headers=self.headers)
            response = connection.getresponse()
            if response.status != 200:
                result.error = f"HTTP {response.status}"
                return result
            last_token_at = None
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    result.prompt_tokens = chunk["usage"]["prompt_tokens"]
                    result.output_tokens = chunk["usage"]["completion_tokens"]
                choices = chunk.get("choices") or []
                if choices and choices[0].get("delta", {}).get("content"):
                    now = time.perf_counter()
                    if last_token_at is None:
                        result.ttft = now - started
                    else:
                        result.inter_token.append(now - last_token_at)
                    last_token_at = now
            result.latency = time.perf_counter() - started
            result.ok = last_token_at is not None
            if not result.ok:
                result.error = "empty response"
        except (OSError, http.client.HTTPException, ValueError) as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            connection.close()
        return result


def send_parallel(client: Client, prompts: list[str], concurrency: int, max_tokens: int | None = None):
    lock = threading.Lock()
    results: list[RequestResult] = []

    def worker(prompt: str) -> None:
        outcome = client.send(prompt, max_tokens)
        with lock:
            results.append(outcome)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(worker, prompts))
    return results, time.perf_counter() - started


def warm_up(client: Client, concurrency: int, prompt_tokens: int, rng: random.Random) -> None:
    prompts = [build_prompt(WARMUP_PROMPT_TOKENS, rng) for _ in range(concurrency)]
    send_parallel(client, prompts, concurrency, max_tokens=8)
    client.send(build_prompt(prompt_tokens, rng), max_tokens=4)


def summarize(scenario: str, concurrency: int, results: list[RequestResult], duration: float) -> ScenarioReport:
    ok = [r for r in results if r.ok]
    tpot = [(r.latency - r.ttft) / (r.output_tokens - 1) for r in ok if r.output_tokens > 1]
    itl = [gap for r in ok for gap in r.inter_token]
    output_tokens = sum(r.output_tokens for r in ok)
    prompt_tokens = sum(r.prompt_tokens for r in ok)
    return ScenarioReport(
        scenario=scenario,
        concurrency=concurrency,
        requests=len(ok),
        errors=len(results) - len(ok),
        duration_s=round(duration, 2),
        mean_prompt_tokens=round(prompt_tokens / len(ok), 1) if ok else 0.0,
        mean_output_tokens=round(output_tokens / len(ok), 1) if ok else 0.0,
        ttft_p50_ms=round(percentile([r.ttft for r in ok], 0.5) * 1000, 1),
        ttft_p95_ms=round(percentile([r.ttft for r in ok], 0.95) * 1000, 1),
        latency_p50_s=round(percentile([r.latency for r in ok], 0.5), 2),
        latency_p95_s=round(percentile([r.latency for r in ok], 0.95), 2),
        tpot_p50_ms=round(percentile(tpot, 0.5) * 1000, 1),
        itl_p95_ms=round(percentile(itl, 0.95) * 1000, 1),
        request_throughput=round(len(ok) / duration, 3),
        output_throughput=round(output_tokens / duration, 1),
        total_throughput=round((output_tokens + prompt_tokens) / duration, 1),
    )


def run_scenario(client: Client, scenario: Scenario, concurrency: int, total: int, seed: int) -> ScenarioReport:
    rng = random.Random(f"{seed}-{scenario.name}-{concurrency}")
    warm_up(client, concurrency, scenario.prompt_tokens, rng)
    prompts = [build_prompt(scenario.prompt_tokens, rng) for _ in range(total)]
    results, duration = send_parallel(client, prompts, concurrency)
    errors = [r.error for r in results if not r.ok]
    if errors:
        print(f"  {len(errors)} failed requests, first error: {errors[0]}", file=sys.stderr)
    return summarize(scenario.name, concurrency, results, duration)


def markdown_table(reports: list[ScenarioReport]) -> str:
    header = (
        "| Scenario | Conc. | Req | In tok | Out tok | TTFT p50, ms | TTFT p95, ms | "
        "Latency p50, s | Latency p95, s | TPOT p50, ms | Req/s | Out tok/s | Total tok/s |"
    )
    lines = [header, "|" + "---|" * 13]
    for r in reports:
        lines.append(
            f"| {r.scenario} | {r.concurrency} | {r.requests} | {r.mean_prompt_tokens:.0f} | "
            f"{r.mean_output_tokens:.0f} | {r.ttft_p50_ms:.0f} | {r.ttft_p95_ms:.0f} | "
            f"{r.latency_p50_s:.2f} | {r.latency_p95_s:.2f} | {r.tpot_p50_ms:.1f} | "
            f"{r.request_throughput:.3f} | {r.output_throughput:.1f} | {r.total_throughput:.1f} |"
        )
    return "\n".join(lines)


def env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Closed-loop load test for an OpenAI-compatible vLLM endpoint.")
    parser.add_argument("--base-url", default=env("BASE_URL", "http://localhost:8000"))
    parser.add_argument(
        "--plan",
        default=env("BENCH_PLAN", "short=64@1,4,8 long=1024@1,4"),
        help="space separated name=prompt_tokens@concurrency,... items",
    )
    parser.add_argument("--requests-per-worker", type=int, default=int(env("BENCH_REQUESTS_PER_WORKER", "2")))
    parser.add_argument("--min-requests", type=int, default=int(env("BENCH_MIN_REQUESTS", "4")))
    parser.add_argument("--max-tokens", type=int, default=int(env("BENCH_MAX_TOKENS", "128")))
    parser.add_argument("--timeout", type=float, default=float(env("BENCH_TIMEOUT", "1800")))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", help="write JSON report to this file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("VLLM_API_KEY") or os.environ.get("API_KEY")
    if not api_key:
        print("VLLM_API_KEY is not set", file=sys.stderr)
        return 2

    client = Client(args.base_url, api_key, args.max_tokens, args.timeout)
    model = client.discover_model()
    print(f"model: {model}, plan: {args.plan}", file=sys.stderr, flush=True)

    reports = []
    for scenario in parse_plan(args.plan):
        for concurrency in scenario.concurrency:
            total = max(args.min_requests, concurrency * args.requests_per_worker)
            print(f"{scenario.name} x{concurrency}: {total} requests", file=sys.stderr, flush=True)
            reports.append(run_scenario(client, scenario, concurrency, total, args.seed))
            print("  done " + markdown_table(reports[-1:]).splitlines()[-1], file=sys.stderr, flush=True)

    print(markdown_table(reports))
    payload = {
        "model": model,
        "plan": args.plan,
        "max_tokens": args.max_tokens,
        "reports": [asdict(r) for r in reports],
    }
    print("JSON " + json.dumps(payload))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    return 0 if all(r.errors == 0 for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
