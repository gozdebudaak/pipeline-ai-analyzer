import json
import logging

from app.core.logging import JsonFormatter, configure_logging, correlation_id_var


def _make_record(msg: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_record_is_serialized_as_json() -> None:
    line = JsonFormatter().format(_make_record("hello"))

    payload = json.loads(line)
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert "timestamp" in payload
    assert "correlation_id" not in payload


def test_correlation_id_from_context_is_attached() -> None:
    token = correlation_id_var.set("abc-123")
    try:
        payload = json.loads(JsonFormatter().format(_make_record("hello")))
    finally:
        correlation_id_var.reset(token)

    assert payload["correlation_id"] == "abc-123"


def test_extra_fields_become_top_level_keys() -> None:
    payload = json.loads(JsonFormatter().format(_make_record("done", duration_ms=42)))

    assert payload["duration_ms"] == 42


def test_configure_logging_is_idempotent() -> None:
    configure_logging("DEBUG")
    configure_logging("INFO")

    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.INFO
