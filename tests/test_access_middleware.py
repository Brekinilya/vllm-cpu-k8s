import asyncio
import json
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vllm_privacy.middleware import AccessLogMiddleware

CANARY = "CANARY-7f3a91"


def make_app(status=200, body=b'{"ok":true}'):
    async def app(scope, receive, send):
        while (await receive()).get("more_body"):
            pass
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"x-request-id", b"req-42")],
        })
        await send({"type": "http.response.body", "body": body})

    return app


def run_request(app, path, body=b""):
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": f"q={CANARY}".encode(),
        "headers": [(b"authorization", f"Bearer {CANARY}".encode())],
        "client": ("10.0.0.7", 51234),
    }
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return messages.pop(0)

    async def send(message):
        pass

    asyncio.run(AccessLogMiddleware(app)(scope, receive, send))


class AccessLogMiddlewareTest(unittest.TestCase):
    def capture(self):
        return self.assertLogs("vllm_privacy.access", level="INFO")

    def test_logs_request_fact_without_content(self):
        payload = json.dumps({"messages": [{"role": "user", "content": CANARY}]}).encode()
        with self.capture() as logs:
            run_request(make_app(), "/v1/chat/completions", payload)
        self.assertEqual(len(logs.output), 1)
        line = logs.output[0]
        self.assertIn("10.0.0.7 POST /v1/chat/completions status=200", line)
        self.assertIn("request_id=req-42", line)
        self.assertNotIn(CANARY, line)

    def test_skips_successful_probe_requests(self):
        logger = logging.getLogger("vllm_privacy.access")
        with self.assertNoLogs(logger, level="INFO"):
            run_request(make_app(), "/health")

    def test_logs_failed_probe_requests(self):
        with self.capture() as logs:
            run_request(make_app(status=503), "/health")
        self.assertIn("status=503", logs.output[0])

    def test_logs_rejected_requests(self):
        with self.capture() as logs:
            run_request(make_app(status=401, body=b'{"error":"Unauthorized"}'), "/v1/completions")
        self.assertIn("status=401", logs.output[0])


if __name__ == "__main__":
    unittest.main()
