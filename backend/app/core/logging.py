"""JSON logging with request correlation and defense-in-depth redaction."""

from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pythonjsonlogger.json import JsonFormatter

from app.core.middleware import request_id_ctx

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:password|secret|token|api[_-]?key|service[_-]?role[_-]?key|authorization|cookie|credential|"
    r"private[_-]?key|database[_-]?url|redis[_-]?url|connection[_-]?string|dsn)",
    re.IGNORECASE,
)
_PRIVATE_PATH_KEY = re.compile(r"(?:file_?path|local_?path|storage_?path|filename)", re.IGNORECASE)
_BEARER_TOKEN = re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"([\"']?(?:password|secret|token|access[_-]?token|refresh[_-]?token|"
    r"api[_-]?key|service[_-]?role[_-]?key|authorization|client[_-]?secret|"
    r"database[_-]?url|redis[_-]?url|connection[_-]?string)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&}]+)",
    re.IGNORECASE,
)
_CONNECTION_CREDENTIALS = re.compile(
    r"\b([a-z][a-z0-9+.-]*://[^:/\s]+:)[^@/\s]+(@)", re.IGNORECASE
)
_SECRET_QUERY_PARAMETER = re.compile(
    r"([?&](?:apikey|api_key|access_token|refresh_token|token|password)=)[^&\s]+",
    re.IGNORECASE,
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_COMMON_API_KEY = re.compile(r"\b(?:sk-(?:live|test|proj)-|sb_secret_|pcsk_)[A-Za-z0-9_-]{12,}\b")
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_TRACEBACK_FRAME_PATH = re.compile(r"(?m)^(\s*File\s+)(?:\"[^\"]*\"|'[^']*')(?=, line \d+)")
_QUOTED_ABSOLUTE_PATH = re.compile(
    r"(?P<quote>[\"'])(?:[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|/(?!/))[^\"'\r\n]*(?P=quote)"
)


def redact_sensitive_text(text: str) -> str:
    """Mask common credentials and secrets embedded in free-form log text."""
    redacted = _BEARER_TOKEN.sub("Bearer " + _REDACTED, text)
    redacted = _SECRET_ASSIGNMENT.sub(r"\1" + _REDACTED, redacted)
    redacted = _CONNECTION_CREDENTIALS.sub(r"\1" + _REDACTED + r"\2", redacted)
    redacted = _SECRET_QUERY_PARAMETER.sub(r"\1" + _REDACTED, redacted)
    redacted = _JWT.sub(_REDACTED, redacted)
    redacted = _COMMON_API_KEY.sub(_REDACTED, redacted)
    return _PRIVATE_KEY_BLOCK.sub(_REDACTED, redacted)


def _redact_exception_text(text: str) -> str:
    """Redact secrets and absolute local paths from exception diagnostics."""
    text = redact_sensitive_text(text)
    text = _TRACEBACK_FRAME_PATH.sub(r'\1"[PATH]"', text)
    return _QUOTED_ABSOLUTE_PATH.sub("'[PATH]'", text)


def _safe_value(value: Any, *, key: str | None = None) -> Any:
    """Return a JSON-safe, recursively scrubbed value for a log field."""
    if key and _SENSITIVE_KEY.search(key):
        return _REDACTED
    if key and _PRIVATE_PATH_KEY.search(key):
        return "[PATH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if key and key.lower() in {"error", "error_message", "exception", "exception_text", "traceback"}:
            return _redact_exception_text(value)
        return redact_sensitive_text(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return "[BINARY OMITTED]"
    if isinstance(value, BaseException):
        return _redact_exception_text(str(value))
    if isinstance(value, Enum):
        return _safe_value(value.value)
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for field, field_value in value.items():
            field_name = str(field)
            safe[field_name] = _safe_value(field_value, key=field_name)
        return safe
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_value(item) for item in value]
    return redact_sensitive_text(str(value))


class SensitiveDataFilter(logging.Filter):
    """Scrub messages, arguments, extras, stack details, and exception traces."""

    _STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _safe_value(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_safe_value(value) for value in record.args)
        elif isinstance(record.args, Mapping):
            record.args = _safe_value(record.args)

        for field_name, value in tuple(record.__dict__.items()):
            if field_name not in self._STANDARD_RECORD_FIELDS and not field_name.startswith("_"):
                record.__dict__[field_name] = _safe_value(value, key=field_name)

        if record.exc_info:
            exception_text = "".join(traceback.format_exception(*record.exc_info))
            record.exception_text = _redact_exception_text(exception_text)
            record.exc_info = None
            record.exc_text = None
        elif record.exc_text:
            record.exception_text = _redact_exception_text(record.exc_text)
            record.exc_text = None

        if record.stack_info:
            record.stack_info = _redact_exception_text(record.stack_info)
        return True


class RequestIdFilter(logging.Filter):
    """Inject the active request ID into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get() or None
        return True


class JsonLogFormatter(JsonFormatter):
    """Emit a consistent JSON schema using python-json-logger."""

    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
            rename_fields={
                "asctime": "timestamp",
                "levelname": "level",
                "name": "logger",
            },
        )

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = datetime.fromtimestamp(record.created, timezone.utc).isoformat()
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        request_id = getattr(record, "request_id", None) or request_id_ctx.get()
        if request_id:
            log_record["request_id"] = request_id
        else:
            log_record.pop("request_id", None)


def configure_logging(*, level: int = logging.INFO, as_json: bool = True) -> None:
    """Configure JSON-by-default logging for app, Uvicorn, and worker records."""
    handler = logging.StreamHandler()
    handler.addFilter(SensitiveDataFilter())
    handler.addFilter(RequestIdFilter())
    if as_json:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(request_id)s%(message)s")
        )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Uvicorn/Celery install their own handlers during startup. Propagate their
    # records through this same formatter/filter chain so container logs stay JSON.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery", "celery.task"):
        framework_logger = logging.getLogger(logger_name)
        framework_logger.handlers.clear()
        framework_logger.propagate = True
        framework_logger.disabled = False
