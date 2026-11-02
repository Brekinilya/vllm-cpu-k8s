import logging
import traceback
from collections.abc import Mapping

REDACTED = "<redacted>"

REQUEST_LOGGER = "vllm.entrypoints.serve.utils.request_logger"
RESPONSE_BODY_LOGGER = "vllm.entrypoints.serve.middleware.log_response"
CONTENT_NAMESPACES = (
    "vllm.entrypoints",
    "vllm.tool_parsers",
    "vllm.reasoning",
    "uvicorn.error",
)
ARGUMENT_NAMESPACES = ("vllm.tool_parsers", "vllm.reasoning")


def in_namespace(name: str, namespaces: tuple[str, ...]) -> bool:
    return any(name == ns or name.startswith(ns + ".") for ns in namespaces)


def redact_value(value):
    if isinstance(value, (str, bytes)):
        return REDACTED
    return value


def redact_args(args):
    if isinstance(args, Mapping):
        return {key: redact_value(value) for key, value in args.items()}
    if isinstance(args, tuple):
        return tuple(redact_value(value) for value in args)
    return args


def redact_exception(record: logging.LogRecord) -> None:
    if not record.exc_info or record.exc_info[0] is None:
        return
    exc_type, _, tb = record.exc_info
    frames = "".join(traceback.format_tb(tb))
    record.exc_info = None
    record.exc_text = (
        f"Traceback (most recent call last):\n{frames}{exc_type.__qualname__}: {REDACTED}"
    )


def rewrite_request_record(record: logging.LogRecord) -> bool:
    if not record.args:
        return True
    template = record.msg if isinstance(record.msg, str) else ""
    args = record.args if isinstance(record.args, tuple) else ()

    if template.startswith("Received request ") and len(args) >= 2:
        request_id, params = args[0], args[1]
        record.msg = "Received request %s: max_tokens=%s"
        record.args = (request_id, getattr(params, "max_tokens", None))
        return True

    if template.startswith("Generated response ") and "output:" in template and len(args) >= 4:
        request_id, stream_info, output, finish_reason = args[:4]
        record.msg = "Generated response %s%s: output_chars=%d, finish_reason=%s"
        record.args = (request_id, stream_info, len(output or ""), finish_reason)
        return True

    return False


class PrivacyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        if name == RESPONSE_BODY_LOGGER:
            return False
        if name == REQUEST_LOGGER:
            return rewrite_request_record(record)
        if in_namespace(name, CONTENT_NAMESPACES):
            if record.levelno < logging.INFO:
                return False
            redact_exception(record)
        if in_namespace(name, ARGUMENT_NAMESPACES):
            record.args = redact_args(record.args)
        return True
