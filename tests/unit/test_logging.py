"""Structured logging fields, context propagation, and secret redaction."""

import json
import logging

from app.core.logging import JsonFormatter, bind_request_id, reset_request_id


def test_json_formatter_adds_process_and_request_context():
    formatter = JsonFormatter(service="api", environment="local")
    token = bind_request_id("request-123")
    try:
        record = logging.LogRecord(
            "app.test", logging.INFO, __file__, 1, "completed", (), None
        )
        record.operation = "/events"
        record.status = "200"
        payload = json.loads(formatter.format(record))
    finally:
        reset_request_id(token)

    assert payload["service"] == "api"
    assert payload["environment"] == "local"
    assert payload["request_id"] == "request-123"
    assert payload["operation"] == "/events"
    assert payload["status"] == "200"
    assert payload["timestamp"].endswith("Z")


def test_message_redacts_bearer_and_websocket_ticket():
    formatter = JsonFormatter(service="websocket", environment="doks")
    record = logging.LogRecord(
        "app.test",
        logging.WARNING,
        __file__,
        1,
        (
            "Bearer secret.jwt.value ticketbooking.ticket.raw-ticket "
            "password=secret Cookie: refresh_token=raw"
        ),
        (),
        None,
    )
    payload = json.loads(formatter.format(record))
    assert "secret.jwt.value" not in payload["message"]
    assert "raw-ticket" not in payload["message"]
    assert "password=secret" not in payload["message"]
    assert "refresh_token=raw" not in payload["message"]
    assert payload["message"].count("[REDACTED]") == 4


def test_unapproved_extra_fields_are_not_serialized():
    formatter = JsonFormatter(service="api", environment="local")
    record = logging.LogRecord(
        "app.test", logging.INFO, __file__, 1, "safe", (), None
    )
    record.password = "do-not-log"
    record.email = "private@example.com"
    payload = json.loads(formatter.format(record))
    assert "password" not in payload
    assert "email" not in payload
    assert "do-not-log" not in json.dumps(payload)
