import logging
import os
import time

logger = logging.getLogger("vllm_privacy.access")

DEFAULT_SKIP_PATHS = "/health,/metrics,/ping"


def header_value(headers, name: bytes) -> str | None:
    for key, value in headers or ():
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def skip_paths() -> frozenset[str]:
    raw = os.environ.get("ACCESS_LOG_SKIP_PATHS", DEFAULT_SKIP_PATHS)
    return frozenset(path.strip() for path in raw.split(",") if path.strip())


class AccessLogMiddleware:
    def __init__(self, app):
        self.app = app
        self.skip = skip_paths()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        state = {
            "status": 500,
            "bytes": 0,
            "request_id": header_value(scope.get("headers"), b"x-request-id"),
        }

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["request_id"] = state["request_id"] or header_value(
                    message.get("headers"), b"x-request-id"
                )
            elif message["type"] == "http.response.body":
                state["bytes"] += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if path not in self.skip or state["status"] >= 400:
                client = scope.get("client") or ("-", 0)
                logger.info(
                    "%s %s %s status=%d duration_ms=%.1f response_bytes=%d request_id=%s",
                    client[0],
                    scope.get("method", "-"),
                    path,
                    state["status"],
                    (time.perf_counter() - started) * 1000,
                    state["bytes"],
                    state["request_id"] or "-",
                )
