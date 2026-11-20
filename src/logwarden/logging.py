"""Logging setup that passes the logwarden standard, plus helpers for failure logs.

    from logwarden.logging import configure
    log = configure(service_name="billing-api", service_version="1.4.2", environment="production")
    log.info("import_completed", rows_imported=12)

Needs the extra: pip install 'logwarden[structlog]'. See docs/standard.md, "Reference setup".
"""
from __future__ import annotations

import json as json_mod
import logging
import os
import sys
import traceback
from typing import Any, Iterable, TextIO

try:
    import structlog
except ImportError as e:  # pragma: no cover - exercised in a venv without the extra
    raise ImportError("logwarden.logging needs structlog: pip install 'logwarden[structlog]'") from e

from logwarden.patterns import REDACTED, luhn_valid, redact_text, redact_value  # noqa: F401

__all__ = ["configure", "extract_error_code", "extract_error_message", "scrub",
           "ENVIRONMENTS", "LIBRARY_EVENT", "DEFAULT_QUIET", "DEFAULT_SILENCE_PATHS"]

ENVIRONMENTS = ("development", "staging", "test", "production")
LIBRARY_EVENT = "library_log"
DEFAULT_QUIET = ("urllib3", "httpx", "httpcore", "botocore", "boto3", "s3transfer", "asyncio",
                 "sqlalchemy.engine", "aiokafka", "kafka", "openai._base_client", "anthropic._base_client",
                 "multipart", "watchfiles")
DEFAULT_SILENCE_PATHS = ("/health", "/healthz", "/livez", "/readyz", "/ready", "/metrics")
# Loggers that install their own handlers (and plain-text formats); they go through ours instead.
DEFAULT_CAPTURE = ("uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn", "gunicorn.error",
                   "gunicorn.access")
_ACCESS_LOGGERS = ("uvicorn.access", "gunicorn.access")
_MARK = "_logwarden_handler"


# ---------------------------------------------------------------- processors

def _resource(name: str, version: str, environment: str):
    def add_service_resource(_, __, event_dict):
        event_dict.setdefault("service.name", name)
        event_dict.setdefault("service.version", version)
        event_dict.setdefault("deployment.environment.name", environment)
        return event_dict
    return add_service_resource


def add_otel_trace_context(_, __, event_dict):
    """trace_id / span_id from the active OpenTelemetry span (W3C hex); nothing without a span."""
    try:
        from opentelemetry import trace
    except ImportError:
        return event_dict
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict.setdefault("trace_id", format(ctx.trace_id, "032x"))
        event_dict.setdefault("span_id", format(ctx.span_id, "016x"))
    return event_dict


def _exception_type(exc: BaseException) -> str:
    cls = type(exc)
    return cls.__qualname__ if cls.__module__ == "builtins" else f"{cls.__module__}.{cls.__qualname__}"


def exception_to_semconv(_, __, event_dict):
    """exc_info=True / log.exception -> exception.type, exception.message, exception.stacktrace."""
    exc_info = event_dict.pop("exc_info", None)
    if not exc_info:
        return event_dict
    if exc_info is True:
        exc_info = sys.exc_info()
    elif isinstance(exc_info, BaseException):
        exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
    if not isinstance(exc_info, tuple) or exc_info[1] is None:
        return event_dict
    exc = exc_info[1]
    event_dict.setdefault("exception.type", _exception_type(exc))
    event_dict.setdefault("exception.message", str(exc)[:2000])
    event_dict.setdefault("exception.stacktrace", "".join(traceback.format_exception(*exc_info)))
    return event_dict


def _library_event(_, __, event_dict):
    """Third-party (stdlib) records: constant event, original text kept in log.record.original."""
    if event_dict.get("_from_structlog") is False:  # set by ProcessorFormatter for stdlib records
        text = event_dict.get("event")
        event_dict["event"] = LIBRARY_EVENT
        event_dict["log.record.original"] = text if isinstance(text, str) else str(text)
    return event_dict


def _redactor(keep: frozenset[str]):
    def redact_sensitive(_, __, event_dict):
        for key, value in list(event_dict.items()):
            if key in ("event", "logger", "level", "timestamp") or key.startswith("_"):
                continue
            event_dict[key] = redact_value(key, value, keep)
        if isinstance(event_dict.get("event"), str):
            event_dict["event"] = redact_text(event_dict["event"])
        return event_dict
    return redact_sensitive


def _silencer(paths: frozenset[str]):
    def drop_silenced_paths(_, __, event_dict):
        if "_record" in event_dict:  # stdlib record: filtered by _SilenceAccess on the handler
            return event_dict
        for key in ("http.route", "url.path"):
            if event_dict.get(key) in paths:
                raise structlog.DropEvent
        return event_dict
    return drop_silenced_paths


class _SilenceAccess(logging.Filter):
    """Drop access-log lines for health/metrics paths (uvicorn/gunicorn access loggers)."""

    def __init__(self, paths: frozenset[str]):
        super().__init__()
        self.paths = paths

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith(_ACCESS_LOGGERS):
            return True
        args = record.args if isinstance(record.args, tuple) else ()
        candidates = [a for a in args if isinstance(a, str) and a.startswith("/")]
        if not candidates:
            msg = record.getMessage()
            candidates = [w.split("?")[0] for w in msg.split() if w.startswith("/")]
        return not any(c.split("?")[0] in self.paths for c in candidates)


def _drop_meta(_, __, event_dict):
    event_dict.pop("_record", None)
    event_dict.pop("_from_structlog", None)
    return event_dict


def _callsite():
    """code.file.path / code.line.number / code.function.name of the logging call (semconv)."""
    cp = structlog.processors.CallsiteParameter
    adder = structlog.processors.CallsiteParameterAdder(
        {cp.PATHNAME, cp.LINENO, cp.FUNC_NAME, cp.MODULE}, additional_ignores=["logwarden.logging"])
    cwd = os.getcwd()

    def add_code_location(logger, name, event_dict):
        event_dict = adder(logger, name, event_dict)
        path = event_dict.pop("pathname", None)
        line = event_dict.pop("lineno", None)
        func = event_dict.pop("func_name", None)
        module = event_dict.pop("module", None)
        if isinstance(path, str) and path:
            try:
                rel = os.path.relpath(path, cwd)
            except ValueError:      # another drive on Windows
                rel = path
            # Relative to the working directory when inside it, so it matches the repository.
            event_dict["code.file.path"] = (path if rel.startswith("..") else rel).replace("\\", "/")
        if isinstance(line, int):
            event_dict["code.line.number"] = line
        if isinstance(func, str) and func:
            event_dict["code.function.name"] = f"{module}.{func}" if module and func != "<module>" else func
        return event_dict
    return add_code_location


def _json_default(o: Any) -> str:
    return str(o)


# ---------------------------------------------------------------- configure

def _from_resource_attributes(key: str) -> str | None:
    for pair in os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(","):
        k, _, v = pair.partition("=")
        if k.strip() == key and v.strip():
            return v.strip()
    return None


def configure(service_name: str | None = None, service_version: str | None = None,
              environment: str | None = None, *, level: str | int = "INFO", json: bool = True,
              stream: TextIO | None = None, keep: Iterable[str] = (),
              quiet: Iterable[str] = DEFAULT_QUIET, silence_paths: Iterable[str] = DEFAULT_SILENCE_PATHS,
              capture: Iterable[str] = DEFAULT_CAPTURE, callsite: bool | None = None) -> Any:
    """Set up structlog and stdlib logging to write standard-compliant JSON lines.

    service_name, service_version, environment: the resource fields on every line. They
        default to OTEL_SERVICE_NAME, SERVICE_VERSION and DEPLOYMENT_ENVIRONMENT (or
        service.name / service.version / deployment.environment.name in
        OTEL_RESOURCE_ATTRIBUTES). environment must be one of ENVIRONMENTS.
    level: lowest level written. json=False: console output for local development only.
    keep: field names never redacted (e.g. "user.email" when it is required).
    quiet: loggers set to WARNING. silence_paths: health/metrics paths dropped from logs.
    capture: loggers whose own handlers are removed so they go through this setup.
    callsite: add code.file.path, code.line.number and code.function.name (where the line
        was logged), for `logwarden check --sarif`. Default: LOGWARDEN_CALLSITE=1, which the
        pytest plugin sets while capturing.

    Safe to call again (e.g. in tests): it replaces its own handler. Returns a structlog logger.
    Run uvicorn with log_config=None so it doesn't install its own handlers afterwards.
    """
    name = service_name or os.environ.get("OTEL_SERVICE_NAME") or _from_resource_attributes("service.name")
    version = (service_version or os.environ.get("SERVICE_VERSION")
               or _from_resource_attributes("service.version"))
    env = (environment or os.environ.get("DEPLOYMENT_ENVIRONMENT")
           or _from_resource_attributes("deployment.environment.name"))
    missing = [k for k, v in (("service_name", name), ("service_version", version), ("environment", env)) if not v]
    if missing:
        raise ValueError(f"logwarden.logging.configure: {', '.join(missing)} not set "
                         "(pass it, or set OTEL_SERVICE_NAME / SERVICE_VERSION / DEPLOYMENT_ENVIRONMENT)")
    if env not in ENVIRONMENTS:
        raise ValueError(f"logwarden.logging.configure: environment {env!r} must be one of "
                         f"{', '.join(ENVIRONMENTS)} (standard §3)")

    paths = frozenset(silence_paths)
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        _library_event,
        _resource(name, version, env),
        add_otel_trace_context,
        exception_to_semconv,
        _redactor(frozenset(keep)),
        _silencer(paths),
    ]
    if callsite is None:
        callsite = os.environ.get("LOGWARDEN_CALLSITE", "").lower() in ("1", "true", "yes")
    if callsite:
        shared.insert(3, _callsite())
    renderer = (structlog.processors.JSONRenderer(default=_json_default, ensure_ascii=False) if json
                else structlog.dev.ConsoleRenderer())
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared, processors=[_drop_meta, renderer])

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(_SilenceAccess(paths))
    setattr(handler, _MARK, True)

    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _MARK, False):
            root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level if isinstance(level, int) else logging.getLevelName(str(level).upper()))

    for logger_name in capture:
        lg = logging.getLogger(logger_name)
        lg.handlers.clear()
        lg.propagate = True
    for logger_name in quiet:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    return structlog.get_logger()


# ---------------------------------------------------------------- failure helpers

def scrub(text: Any, limit: int = 500) -> str:
    """Redact secrets/credentials/emails/card numbers in text and cut it to `limit` chars."""
    s = text if isinstance(text, str) else str(text)
    s = redact_text(s)
    return s if len(s) <= limit else s[: max(limit - 1, 0)] + "…"


def _body(response: Any) -> Any:
    """Parsed JSON body of an httpx/requests response, a dict/list, or text."""
    if isinstance(response, (dict, list)):
        return response
    if isinstance(response, (bytes, bytearray)):
        response = response.decode("utf-8", errors="replace")
    if isinstance(response, str):
        try:
            return json_mod.loads(response)
        except ValueError:
            return response
    for attr in ("json",):
        fn = getattr(response, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # not JSON
                break
    text = getattr(response, "text", None)
    return text if isinstance(text, str) else None


def _first(*values: Any) -> Any:
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


def extract_error_code(response: Any) -> str | None:
    """A short stable error code from an upstream error body, for `error.type`.

    Handles OpenAI ({"error": {"code", "type"}}), Anthropic ({"error": {"type"}}),
    {"code": ...}, {"error_code": ...}, {"error": "some_code"}. None if there is none.
    """
    body = _body(response)
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    nested = err if isinstance(err, dict) else {}
    code = _first(nested.get("code"), nested.get("type"), body.get("code"), body.get("error_code"),
                  err if isinstance(err, str) and len(err) <= 64 and " " not in err else None)
    if code is None:
        return None
    return scrub(code, 64)


def extract_error_message(response: Any, limit: int = 500) -> str | None:
    """The upstream's error message, scrubbed and at most `limit` chars, for
    `upstream_error_message`. Never the whole raw body of a large response."""
    body = _body(response)
    if body is None:
        return None
    if isinstance(body, dict):
        err = body.get("error")
        nested = err if isinstance(err, dict) else {}
        msg = _first(nested.get("message"), body.get("message"), body.get("detail"),
                     err if isinstance(err, str) else None, body.get("error_description"),
                     body.get("title"))
        if msg is None:
            msg = json_mod.dumps(body, ensure_ascii=False)
    else:
        msg = body
    if not isinstance(msg, str):
        msg = json_mod.dumps(msg, ensure_ascii=False)
    return scrub(msg, limit)
