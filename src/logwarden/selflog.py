"""logwarden's own messages on stderr: plain text, or JSON records that pass the standard.

LOGWARDEN_LOG_FORMAT=json (or `--log-format json`) turns every message into one JSON line with
the required fields, a constant event name and the text in `logwarden_detail` (declared in
logwarden's registry). Records go to stderr: stdout carries the result (`--json`, MCP).
No dependencies: the core install has none.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

ENV = "LOGWARDEN_LOG_FORMAT"
FORMATS = ("text", "json")
ENVIRONMENTS = ("development", "staging", "test", "production")
LIBRARY_EVENT = "library_log"
_format: str | None = None


def set_format(fmt: str | None) -> None:
    global _format
    _format = fmt


def fmt() -> str:
    f = (_format or os.environ.get("LOGWARDEN_LOG_FORMAT") or "text").strip().lower()
    return f if f in FORMATS else "text"


def _resource_attr(key: str) -> str | None:
    for pair in os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(","):
        k, _, v = pair.partition("=")
        if k.strip() == key and v.strip():
            return v.strip()
    return None


def _environment() -> str:
    return (os.environ.get("DEPLOYMENT_ENVIRONMENT") or _resource_attr("deployment.environment.name")
            or "production")


def record(event: str, level: str = "info", detail: str | None = None, *, logger: str = "logwarden",
           **fields: Any) -> dict:
    from logwarden import __version__
    rec = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": level,
        "event": event,
        "logger": logger,
        "service.name": "logwarden",
        "service.version": __version__,
        "deployment.environment.name": _environment(),
    }
    if detail is not None:
        rec["logwarden_detail"] = detail
    rec.update({k: v for k, v in fields.items() if v is not None})
    return rec


def emit(event: str, detail: str, level: str = "info", *, error_type: str | None = None,
         exc: BaseException | None = None) -> None:
    """One message. Text mode prints `logwarden: <detail>` as before."""
    if fmt() != "json":
        print(f"logwarden: {detail}", file=sys.stderr, flush=True)
        return
    fields: dict[str, Any] = {"error.type": error_type}
    if exc is not None:
        fields.update({
            "exception.type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "exception.message": str(exc),
            "exception.stacktrace": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        })
        fields["error.type"] = error_type or type(exc).__qualname__
    _write(record(event, level, detail, **fields))


def _write(rec: dict) -> None:
    sys.stderr.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    sys.stderr.flush()


class _LibraryFormatter(logging.Formatter):
    """Third-party records (e.g. the MCP SDK): the standard's library_log event."""

    def format(self, r: logging.LogRecord) -> str:
        level = {"WARNING": "warning", "CRITICAL": "critical"}.get(r.levelname, r.levelname.lower())
        fields: dict[str, Any] = {"log.record.original": r.getMessage()}
        if r.exc_info and r.exc_info[1] is not None:
            e = r.exc_info[1]
            fields.update({"exception.type": f"{type(e).__module__}.{type(e).__qualname__}",
                           "exception.message": str(e),
                           "exception.stacktrace": "".join(traceback.format_exception(*r.exc_info))})
        return json.dumps(record(LIBRARY_EVENT, level, None, logger=r.name, **fields),
                          ensure_ascii=False, default=str)


def route_library_logs(level: int = logging.INFO) -> None:
    """In JSON mode, send stdlib logging (third-party libraries) to stderr as JSON records."""
    if fmt() != "json":
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_LibraryFormatter())
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)
    for name in list(logging.root.manager.loggerDict):   # libraries that installed their own handlers
        lg = logging.getLogger(name)
        if lg.handlers:
            lg.handlers.clear()
            lg.propagate = True
