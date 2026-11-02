import contextlib
import importlib.util
import io
import json
import logging
import logging.config
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

CANARY = "CANARY-7f3a91"


@unittest.skipUnless(importlib.util.find_spec("vllm"), "vllm is not installed")
class VllmRequestLoggerTest(unittest.TestCase):
    def test_real_request_logger_does_not_leak_prompts_or_outputs(self):
        from vllm.entrypoints.serve.utils.request_logger import RequestLogger
        from vllm.sampling_params import SamplingParams

        config = json.loads((SRC / "vllm_privacy" / "logging.json").read_text())
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            logging.config.dictConfig(config)
            logging.getLogger("vllm").setLevel(logging.DEBUG)

            request_logger = RequestLogger(max_log_len=None)
            request_logger.log_inputs(
                "chatcmpl-1",
                f"user secret {CANARY}",
                [101, 102, 103],
                None,
                SamplingParams(max_tokens=8, stop=[CANARY]),
                None,
            )
            request_logger.log_outputs("chatcmpl-1", f"model answer {CANARY}", [7, 8], "stop")
            request_logger.log_outputs(
                "chatcmpl-1", CANARY, [7], None, is_streaming=True, delta=True
            )

        output = stream.getvalue()
        self.assertNotIn(CANARY, output)
        self.assertIn("Received request chatcmpl-1: max_tokens=8", output)
        self.assertIn("Generated response chatcmpl-1: output_chars=", output)
        self.assertIn("(streaming delta)", output)


if __name__ == "__main__":
    unittest.main()
