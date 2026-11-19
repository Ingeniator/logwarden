"""Receive logs over OTLP/HTTP and write them as JSON lines in the standard's shape.

Any service with an OpenTelemetry SDK can then be checked like a Python one:

    logwarden capture -o logs.jsonl -- npm test

Only the standard library is used: OTLP protobuf is decoded by hand (the logs schema is
small), and OTLP JSON is parsed with `json`. gRPC is not supported; exporters must use
`http/protobuf` or `http/json`. Traces and metrics sent to the same endpoint are accepted
and ignored, so a service exporting everything doesn't log export errors.
"""
from __future__ import annotations

import base64
import gzip
import json
import struct
import threading
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator

LEVELS = {"debug", "info", "warning", "error", "critical", "fatal"}
_LEVEL_ALIASES = {"warn": "warning", "trace": "debug", "err": "error", "information": "info",
                  "informational": "info", "notice": "info", "verbose": "debug", "crit": "critical"}
MAX_BODY = 64 * 1024 * 1024


class DecodeError(ValueError):
    pass


class _TooLarge(Exception):
    pass


# --- protobuf wire format --------------------------------------------------------------

def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        if i >= len(buf):
            raise DecodeError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise DecodeError("varint too long")


def _fields(buf: bytes) -> Iterator[tuple[int, int, Any]]:
    """(field number, wire type, value) for each field in a message."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(buf, i)
        elif wt == 1:
            if i + 8 > n:
                raise DecodeError("truncated fixed64")
            v, i = buf[i:i + 8], i + 8
        elif wt == 2:
            ln, i = _varint(buf, i)
            if i + ln > n:
                raise DecodeError("truncated field")
            v, i = buf[i:i + ln], i + ln
        elif wt == 5:
            if i + 4 > n:
                raise DecodeError("truncated fixed32")
            v, i = buf[i:i + 4], i + 4
        else:
            raise DecodeError(f"unsupported wire type {wt}")
        yield num, wt, v


def _str(b: bytes) -> str:
    return b.decode("utf-8", errors="replace")


def _int64(v: int) -> int:
    return v - (1 << 64) if v >= 1 << 63 else v


def _pb_any(buf: bytes) -> Any:
    for num, wt, v in _fields(buf):
        if num == 1 and wt == 2:
            return _str(v)
        if num == 2 and wt == 0:
            return bool(v)
        if num == 3 and wt == 0:
            return _int64(v)
        if num == 4 and wt == 1:
            return struct.unpack("<d", v)[0]
        if num == 5 and wt == 2:
            return [_pb_any(x) for n2, w2, x in _fields(v) if n2 == 1 and w2 == 2]
        if num == 6 and wt == 2:
            return dict(_pb_kv(x) for n2, w2, x in _fields(v) if n2 == 1 and w2 == 2)
        if num == 7 and wt == 2:
            return base64.b64encode(v).decode("ascii")
    return None


def _pb_kv(buf: bytes) -> tuple[str, Any]:
    key, value = "", None
    for num, wt, v in _fields(buf):
        if num == 1 and wt == 2:
            key = _str(v)
        elif num == 2 and wt == 2:
            value = _pb_any(v)
    return key, value


def _pb_attrs(buf: bytes, field: int) -> dict:
    return dict(_pb_kv(v) for num, wt, v in _fields(buf) if num == field and wt == 2)


def _pb_record(buf: bytes) -> dict:
    r: dict[str, Any] = {"attributes": {}}
    for num, wt, v in _fields(buf):
        if num == 1 and wt == 1:
            r["time"] = struct.unpack("<Q", v)[0]
        elif num == 11 and wt == 1:
            r["observed"] = struct.unpack("<Q", v)[0]
        elif num == 2 and wt == 0:
            r["severity_number"] = v
        elif num == 3 and wt == 2:
            r["severity_text"] = _str(v)
        elif num == 5 and wt == 2:
            r["body"] = _pb_any(v)
        elif num == 6 and wt == 2:
            k, val = _pb_kv(v)
            r["attributes"][k] = val
        elif num == 9 and wt == 2:
            r["trace_id"] = v.hex()
        elif num == 10 and wt == 2:
            r["span_id"] = v.hex()
        elif num == 12 and wt == 2:
            r["event_name"] = _str(v)
    return r


def decode_protobuf(buf: bytes) -> list[dict]:
    """ExportLogsServiceRequest -> flat records {resource, scope, record fields}."""
    out = []
    for num, wt, rl in _fields(buf):
        if num != 1 or wt != 2:
            continue
        resource: dict = {}
        scopes = []
        for n2, w2, v in _fields(rl):
            if n2 == 1 and w2 == 2:
                resource = _pb_attrs(v, 1)
            elif n2 == 2 and w2 == 2:
                scopes.append(v)
        for sl in scopes:
            scope = ""
            records = []
            for n3, w3, v in _fields(sl):
                if n3 == 1 and w3 == 2:
                    scope = next((_str(x) for n4, w4, x in _fields(v) if n4 == 1 and w4 == 2), "")
                elif n3 == 2 and w3 == 2:
                    records.append(_pb_record(v))
            for r in records:
                out.append({**r, "resource": resource, "scope": scope})
    return out


# --- OTLP JSON -------------------------------------------------------------------------

def _j(obj: dict, camel: str, snake: str, default: Any = None) -> Any:
    return obj.get(camel, obj.get(snake, default))


def _json_any(v: Any) -> Any:
    if not isinstance(v, dict):
        return v
    if "stringValue" in v or "string_value" in v:
        return _j(v, "stringValue", "string_value")
    if "boolValue" in v or "bool_value" in v:
        return bool(_j(v, "boolValue", "bool_value"))
    if "intValue" in v or "int_value" in v:
        return int(_j(v, "intValue", "int_value"))   # JSON encodes int64 as a string
    if "doubleValue" in v or "double_value" in v:
        return float(_j(v, "doubleValue", "double_value"))
    if "arrayValue" in v or "array_value" in v:
        return [_json_any(x) for x in (_j(v, "arrayValue", "array_value") or {}).get("values", [])]
    if "kvlistValue" in v or "kvlist_value" in v:
        return _json_attrs((_j(v, "kvlistValue", "kvlist_value") or {}).get("values", []))
    if "bytesValue" in v or "bytes_value" in v:
        return _j(v, "bytesValue", "bytes_value")    # already base64
    return None


def _json_attrs(kvs: list) -> dict:
    return {kv.get("key", ""): _json_any(kv.get("value")) for kv in kvs or [] if isinstance(kv, dict)}


def _json_id(v: Any) -> str | None:
    """OTLP/JSON ids are hex; tolerate base64 (protobuf JSON mapping) too."""
    if not isinstance(v, str) or not v:
        return None
    s = v.lower()
    if all(c in "0123456789abcdef" for c in s) and len(s) in (16, 32):
        return s
    try:
        return base64.b64decode(v, validate=True).hex()
    except ValueError:
        return v


def decode_json(text: str | bytes) -> list[dict]:
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise DecodeError(f"invalid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise DecodeError("OTLP JSON must be an object")
    out = []
    for rl in _j(doc, "resourceLogs", "resource_logs", []) or []:
        resource = _json_attrs((rl.get("resource") or {}).get("attributes"))
        for sl in _j(rl, "scopeLogs", "scope_logs", []) or []:
            scope = (sl.get("scope") or {}).get("name", "")
            for lr in _j(sl, "logRecords", "log_records", []) or []:
                r: dict[str, Any] = {"attributes": _json_attrs(lr.get("attributes")),
                                     "resource": resource, "scope": scope}
                for key, camel, snake in (("time", "timeUnixNano", "time_unix_nano"),
                                          ("observed", "observedTimeUnixNano", "observed_time_unix_nano")):
                    if (t := _j(lr, camel, snake)) not in (None, "", "0", 0):
                        r[key] = int(t)
                if (n := _j(lr, "severityNumber", "severity_number")) is not None:
                    r["severity_number"] = n if isinstance(n, int) else _SEV_NAMES.get(str(n), 0)
                if t := _j(lr, "severityText", "severity_text"):
                    r["severity_text"] = t
                if "body" in lr:
                    r["body"] = _json_any(lr["body"])
                for key, camel, snake in (("trace_id", "traceId", "trace_id"), ("span_id", "spanId", "span_id")):
                    if (i := _json_id(_j(lr, camel, snake))) is not None:
                        r[key] = i
                if e := _j(lr, "eventName", "event_name"):
                    r["event_name"] = e
                out.append(r)
    return out


_SEV_NAMES = {f"SEVERITY_NUMBER_{n}{i if i > 1 else ''}": base + i - 1
              for n, base in (("TRACE", 1), ("DEBUG", 5), ("INFO", 9), ("WARN", 13), ("ERROR", 17), ("FATAL", 21))
              for i in range(1, 5)}


# --- record -> standard line -----------------------------------------------------------

def level_of(text: str | None, number: int | None) -> str:
    t = (text or "").strip().lower()
    t = _LEVEL_ALIASES.get(t, t)
    if t in LEVELS:
        return t
    n = number or 0
    if n >= 21:
        return "critical"
    if n >= 17:
        return "error"
    if n >= 13:
        return "warning"
    if n >= 9:
        return "info"
    if n >= 1:
        return "debug"
    return t or "info"


def _iso(ns: int) -> str:
    dt = datetime.fromtimestamp(ns // 1_000_000_000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{(ns % 1_000_000_000) // 1000:06d}Z"


def to_line(r: dict) -> dict:
    """One decoded OTLP record -> one JSON line in the standard's shape."""
    line: dict[str, Any] = {}
    ns = r.get("time") or r.get("observed")
    if ns:
        line["timestamp"] = _iso(ns)
    line["level"] = level_of(r.get("severity_text"), r.get("severity_number"))
    body = r.get("body")
    event = r.get("event_name") or None
    extra: dict[str, Any] = {}
    if isinstance(body, dict) and not event:
        extra = dict(body)                      # structured body: its keys are the fields
        event = extra.pop("event", None) or extra.pop("msg", None) or extra.pop("message", None)
    elif isinstance(body, str) and not event:
        event = body
    elif body not in (None, ""):
        extra["log.record.original"] = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    if event is not None:
        line["event"] = event
    if r.get("scope"):
        line["logger"] = r["scope"]
    # An attribute with an empty AnyValue has no value; don't turn it into a null field.
    for fields in (r.get("resource") or {}, extra, r.get("attributes") or {}):
        line.update({k: v for k, v in fields.items() if v is not None})
    for k in ("trace_id", "span_id"):
        v = r.get(k)
        if v and set(v) != {"0"}:
            line[k] = v
    return line


# --- receiver --------------------------------------------------------------------------

class Receiver:
    """OTLP/HTTP receiver that appends each log record to `out` as a JSON line."""

    def __init__(self, out: Path, host: str = "127.0.0.1", port: int = 4318,
                 log: Callable[[str], None] = lambda m: None):
        self.out = out
        self.count = 0
        self.errors = 0
        self._lock = threading.Lock()
        self._log = log
        out.parent.mkdir(parents=True, exist_ok=True)
        self._file = out.open("w", encoding="utf-8")
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # keep stderr for our own messages
                pass

            def _reply(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> bytes:
                if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                    parts, total = [], 0
                    while True:
                        size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                        if size == 0:
                            while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                                pass  # trailers
                            return b"".join(parts)
                        total += size
                        if total > MAX_BODY:
                            raise _TooLarge
                        parts.append(self.rfile.read(size))
                        self.rfile.readline()  # CRLF after each chunk
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise _TooLarge
                return self.rfile.read(length)

            def do_POST(self) -> None:  # noqa: N802 (http.server API)
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                is_json = ctype == "application/json"
                ok_ctype = "application/json" if is_json else "application/x-protobuf"
                try:
                    raw = self._body()
                except _TooLarge:
                    self._reply(413, b"", "text/plain")
                    return
                except (ValueError, OSError) as e:
                    receiver.errors += 1
                    receiver._log(f"capture: rejected a request: {e}")
                    self._reply(400, b"bad request body", "text/plain")
                    return
                try:
                    enc = (self.headers.get("Content-Encoding") or "").lower()
                    if enc == "gzip":
                        raw = gzip.decompress(raw)
                    elif enc == "deflate":
                        raw = zlib.decompress(raw)
                    path = self.path.split("?")[0].rstrip("/")
                    if path.endswith("/v1/logs"):
                        records = decode_json(raw) if is_json else decode_protobuf(raw)
                        receiver.write(records)
                    elif not (path.endswith("/v1/traces") or path.endswith("/v1/metrics")):
                        self._reply(404, b"", "text/plain")
                        return
                except (DecodeError, OSError, EOFError, zlib.error, ValueError) as e:
                    receiver.errors += 1
                    receiver._log(f"capture: rejected a request: {e}")
                    self._reply(400, str(e).encode(), "text/plain")
                    return
                self._reply(200, b"{}" if is_json else b"", ok_ctype)

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.host, self.port = host, self.server.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def endpoint(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{host}:{self.port}"

    def write(self, records: list[dict]) -> None:
        with self._lock:
            for r in records:
                self._file.write(json.dumps(to_line(r), ensure_ascii=False) + "\n")
                self.count += 1
            self._file.flush()

    def start(self) -> "Receiver":
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        with self._lock:
            self._file.close()
