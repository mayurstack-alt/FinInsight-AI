import json
import logging
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import ApplicationError
from app.core.exceptions import register_exception_handlers
from app.core.logging import (
    JsonLogFormatter,
    RequestIdFilter,
    SensitiveDataFilter,
    redact_sensitive_text,
)
from app.core.middleware import request_id_ctx
from app.workers.celery_app import celery_app


def test_redact_sensitive_text() -> None:
    text = (
        "Connecting with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 and "
        "password=supersecret and service_role_key='sbp_secret123' to "
        "postgresql+psycopg://user:my_db_password@localhost:5432/db"
    )
    redacted = redact_sensitive_text(text)
    assert "supersecret" not in redacted
    assert "sbp_secret123" not in redacted
    assert "my_db_password" not in redacted
    assert "Bearer [REDACTED]" in redacted
    assert "password=[REDACTED]" in redacted
    assert "service_role_key='[REDACTED]'" in redacted
    assert "postgresql+psycopg://user:[REDACTED]@localhost:5432/db" in redacted


def test_sensitive_data_filter_scrubs_record() -> None:
    filt = SensitiveDataFilter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Found Bearer eyJ123456 token with password: secret_pass",
        args=(),
        exc_info=None,
    )
    filt.filter(record)
    assert "eyJ123456" not in record.msg
    assert "secret_pass" not in record.msg
    assert "[REDACTED]" in record.msg


def test_json_log_formatter_outputs_valid_json() -> None:
    formatter = JsonLogFormatter()
    token = request_id_ctx.set("req-test-123")
    try:
        record = logging.LogRecord(
            name="app.test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=20,
            msg="Warning message",
            args=(),
            exc_info=None,
        )
        record.extra_field = "custom_value"
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "WARNING"
        assert data["logger"] == "app.test"
        assert data["message"] == "Warning message"
        assert data["request_id"] == "req-test-123"
        assert data["extra_field"] == "custom_value"
        assert "timestamp" in data
    finally:
        request_id_ctx.reset(token)


def test_exception_handlers_have_consistent_response_shape() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    class Payload(BaseModel):
        num: int

    @app.get("/error-application")
    def route_application_error():
        raise ApplicationError(400, "bad_input", "Input was invalid")

    @app.post("/error-validation")
    def route_validation_error(payload: Payload):
        return {"num": payload.num}

    @app.get("/error-unhandled")
    def route_unhandled_error():
        raise RuntimeError("Something exploded")

    client = TestClient(app, raise_server_exceptions=False)

    # 1. ApplicationError
    res_app = client.get("/error-application")
    assert res_app.status_code == 400
    assert res_app.json() == {"detail": "Input was invalid", "code": "bad_input"}

    # 2. RequestValidationError (Pydantic 422)
    res_val = client.post("/error-validation", json={"num": "not_an_int"})
    assert res_val.status_code == 422
    val_data = res_val.json()
    assert val_data["code"] == "validation_error"
    assert "detail" in val_data
    assert len(val_data["errors"]) > 0
    assert "field" in val_data["errors"][0]

    # 3. Unhandled Exception (500)
    res_500 = client.get("/error-unhandled")
    assert res_500.status_code == 500
    assert res_500.json() == {
        "detail": "Internal server error",
        "code": "internal_error",
    }


def test_celery_worker_configuration() -> None:
    conf = celery_app.conf
    assert conf.task_ignore_result is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.task_acks_late is True
    assert conf.task_default_queue == "fininsight"
    queue_names = [q.name for q in conf.task_queues]
    assert "fininsight" in queue_names
    assert "fininsight_dlq" in queue_names
