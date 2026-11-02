import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vllm_privacy.filters import REQUEST_LOGGER, RESPONSE_BODY_LOGGER, PrivacyFilter

CANARY = "CANARY-7f3a91"


class FakeParams:
    max_tokens = 16
    stop = [CANARY]

    def __repr__(self):
        return f"SamplingParams(max_tokens=16, stop={self.stop!r})"


def make_record(name, msg, args=(), level=logging.INFO, exc_info=None):
    return logging.LogRecord(name, level, __file__, 1, msg, args, exc_info)


def render(record):
    return logging.Formatter("%(message)s").format(record)


class PrivacyFilterTest(unittest.TestCase):
    def setUp(self):
        self.filter = PrivacyFilter()

    def test_received_request_keeps_only_safe_fields(self):
        record = make_record(
            REQUEST_LOGGER,
            "Received request %s: params: %s, lora_request: %s.",
            ("chatcmpl-1", FakeParams(), None),
        )
        self.assertTrue(self.filter.filter(record))
        self.assertEqual(render(record), "Received request chatcmpl-1: max_tokens=16")

    def test_prompt_details_are_dropped(self):
        record = make_record(
            REQUEST_LOGGER,
            "Request %s details: prompt: %r, prompt_token_ids: %s, prompt_embeds shape: %s.",
            ("chatcmpl-1", CANARY, [1, 2], None),
            level=logging.DEBUG,
        )
        self.assertFalse(self.filter.filter(record))

    def test_unknown_request_logger_message_is_dropped(self):
        record = make_record(REQUEST_LOGGER, "Request %s JSON body: %s", ("chatcmpl-1", CANARY))
        self.assertFalse(self.filter.filter(record))

    def test_generated_output_is_replaced_by_length(self):
        record = make_record(
            REQUEST_LOGGER,
            "Generated response %s%s: output: %r, finish_reason: %s",
            ("chatcmpl-1", "", f"answer {CANARY}", "stop"),
        )
        self.assertTrue(self.filter.filter(record))
        message = render(record)
        self.assertNotIn(CANARY, message)
        self.assertIn("output_chars=20", message)
        self.assertIn("finish_reason=stop", message)

    def test_static_request_logger_message_passes(self):
        record = make_record(REQUEST_LOGGER, "`--enable-log-requests` is set")
        self.assertTrue(self.filter.filter(record))

    def test_response_body_logger_is_dropped(self):
        record = make_record(RESPONSE_BODY_LOGGER, "response_body={%s}", (CANARY,))
        self.assertFalse(self.filter.filter(record))

    def test_debug_records_from_entrypoints_are_dropped(self):
        record = make_record(
            "vllm.entrypoints.openai.chat_completion.serving",
            "messages: %s",
            (CANARY,),
            level=logging.DEBUG,
        )
        self.assertFalse(self.filter.filter(record))

    def test_exception_message_is_redacted_but_traceback_kept(self):
        try:
            raise ValueError(f"bad input {CANARY}")
        except ValueError:
            record = make_record(
                "vllm.entrypoints.openai.chat_completion.serving",
                "Error in chat completion",
                level=logging.ERROR,
                exc_info=sys.exc_info(),
            )
        self.assertTrue(self.filter.filter(record))
        message = render(record)
        self.assertNotIn(CANARY, message)
        self.assertIn("ValueError: <redacted>", message)
        self.assertIn("test_exception_message_is_redacted_but_traceback_kept", message)

    def test_tool_parser_string_arguments_are_redacted(self):
        record = make_record(
            "vllm.tool_parsers.hermes_tool_parser",
            "Regex timeout occurred when matching user input: %s",
            (CANARY,),
            level=logging.WARNING,
        )
        self.assertTrue(self.filter.filter(record))
        self.assertNotIn(CANARY, render(record))

    def test_unrelated_records_are_untouched(self):
        record = make_record("vllm.model_executor.model_loader", "Loading weights took %.2f seconds", (1.5,))
        self.assertTrue(self.filter.filter(record))
        self.assertEqual(render(record), "Loading weights took 1.50 seconds")


if __name__ == "__main__":
    unittest.main()
