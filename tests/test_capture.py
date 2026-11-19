"""v0.6.0 claims 1-4: OTLP/HTTP capture."""
import gzip
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, ArrayValue, KeyValue, KeyValueList

from conftest import DATA
from logwarden import capture, check

EMIT = str(DATA / "otlp_emit.py")


def _post(rx, body, ctype="application/x-protobuf", encoding=None, path="/v1/logs"):
    headers = {"Content-Type": ctype}
    if encoding:
        headers["Content-Encoding"] = encoding
    req = urllib.request.Request(rx.endpoint + path, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


@pytest.fixture
def rx(tmp_path):
    r = capture.Receiver(tmp_path / "out.jsonl", port=0).start()
    yield r
    try:
        r.stop()
    except Exception:
        pass


def _lines(rx):
    rx.stop()
    return [json.loads(x) for x in rx.out.read_text(encoding="utf-8").splitlines()]


def _kv(k, **any_value):
    return KeyValue(key=k, value=AnyValue(**any_value))


def _request(*records, resource=None, scope="demo"):
    req = ExportLogsServiceRequest()
    rl = req.resource_logs.add()
    for k, v in (resource or {"service.name": "s", "service.version": "1",
                              "deployment.environment.name": "test"}).items():
        rl.resource.attributes.append(_kv(k, string_value=v))
    sl = rl.scope_logs.add()
    sl.scope.name = scope
    for rec in records:
        sl.log_records.append(rec)
    return req.SerializeToString()


# --- claim 1: real SDK, protobuf -------------------------------------------------------

@pytest.mark.weaver
def test_claim1_python_sdk_protobuf(rx):
    env = {**os.environ, "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": rx.endpoint + "/v1/logs"}
    subprocess.run([sys.executable, EMIT, "good"], env=env, check=True, timeout=60)
    lines = _lines(rx)
    assert [x["event"] for x in lines] == ["upstream_call_completed", "upstream_call_failed"]
    first, second = lines
    assert first["level"] == "info" and second["level"] == "warning"
    assert first["logger"] == "otlp_demo"
    assert first["service.name"] == "otlp-demo" and first["deployment.environment.name"] == "test"
    assert first["server.address"] == "api.example.com"
    assert len(first["trace_id"]) == 32 and len(first["span_id"]) == 16
    assert first["timestamp"].endswith("Z")
    assert second["exception.type"] == "TimeoutError"
    result = check.run("".join(json.dumps(x) + "\n" for x in lines))
    assert result["violations"] == 0, result["findings"]


# --- claim 1/2: OTLP JSON, hand-built ----------------------------------------------------

def test_claim1_json_encoding(rx):
    doc = {"resourceLogs": [{"resource": {"attributes": [
        {"key": "service.name", "value": {"stringValue": "node-demo"}}]},
        "scopeLogs": [{"scope": {"name": "pino"}, "logRecords": [{
            "timeUnixNano": "1700000000123456789", "severityNumber": 17, "severityText": "ERROR",
            "body": {"stringValue": "payment_failed"},
            "traceId": "5b8efff798038103d269b633813fc60c", "spanId": "eee19b7ec3c1b174",
            "attributes": [{"key": "http.response.status_code", "value": {"intValue": "502"}},
                           {"key": "retry", "value": {"boolValue": True}},
                           {"key": "ratio", "value": {"doubleValue": 0.5}},
                           {"key": "time", "value": {}},
                           {"key": "tags", "value": {"arrayValue": {"values": [{"stringValue": "a"}]}}},
                           {"key": "ctx", "value": {"kvlistValue": {"values": [
                               {"key": "k", "value": {"stringValue": "v"}}]}}}]}]}]}]}
    status, body = _post(rx, json.dumps(doc).encode(), "application/json")
    assert status == 200 and body == b"{}"
    (line,) = _lines(rx)
    assert line == {"timestamp": "2023-11-14T22:13:20.123456Z", "level": "error", "event": "payment_failed",
                    "logger": "pino", "service.name": "node-demo", "http.response.status_code": 502,
                    "retry": True, "ratio": 0.5, "tags": ["a"], "ctx": {"k": "v"},
                    "trace_id": "5b8efff798038103d269b633813fc60c", "span_id": "eee19b7ec3c1b174"}


# --- claim 2: conversion details ---------------------------------------------------------

@pytest.mark.parametrize("number,level", [(1, "debug"), (4, "debug"), (5, "debug"), (8, "debug"),
                                          (9, "info"), (12, "info"), (13, "warning"), (16, "warning"),
                                          (17, "error"), (20, "error"), (21, "critical"), (24, "critical"),
                                          (0, "info")])
def test_claim2_severity_number(number, level):
    assert capture.level_of(None, number) == level


@pytest.mark.parametrize("text,level", [("WARN", "warning"), ("Warning", "warning"), ("TRACE", "debug"),
                                        ("ERROR", "error"), ("Information", "info"), ("FATAL", "fatal")])
def test_claim2_severity_text_wins(text, level):
    assert capture.level_of(text, 9) == level


def test_claim2_unknown_text_falls_back_to_number():
    assert capture.level_of("Sev3", 17) == "error"


def test_claim2_protobuf_values_and_body(rx):
    from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord
    recs = [
        LogRecord(time_unix_nano=1_700_000_000_000_000_000, severity_number=9,
                  body=AnyValue(string_value="job_done"),
                  attributes=[_kv("n", int_value=-5), _kv("b", bool_value=False), _kv("d", double_value=1.5),
                              _kv("raw", bytes_value=b"\x01\x02"),
                              _kv("arr", array_value=ArrayValue(values=[AnyValue(int_value=1), AnyValue(string_value="x")])),
                              _kv("map", kvlist_value=KeyValueList(values=[_kv("k", string_value="v")]))],
                  trace_id=bytes.fromhex("0af7651916cd43dd8448eb211c80319c"), span_id=bytes.fromhex("b7ad6b7169203331")),
        # event name set: it is the event; the string body is kept as the original text
        LogRecord(observed_time_unix_nano=1_700_000_001_000_000_000, severity_text="WARN",
                  event_name="retry_scheduled", body=AnyValue(string_value="retrying in 2s")),
        # structured body: its keys become fields, and its event key the event
        LogRecord(severity_number=13, body=AnyValue(kvlist_value=KeyValueList(values=[
            _kv("event", string_value="cache_miss"), _kv("cache.key", string_value="k1")]))),
        # a non-string, non-map body
        LogRecord(severity_number=9, event_name="x_done", body=AnyValue(int_value=7),
                  trace_id=b"\x00" * 16),
    ]
    status, body = _post(rx, _request(*recs))
    assert status == 200 and body == b""
    a, b, c, d = _lines(rx)
    assert a["timestamp"] == "2023-11-14T22:13:20.000000Z" and a["event"] == "job_done" and a["level"] == "info"
    assert (a["n"], a["b"], a["d"], a["raw"], a["arr"], a["map"]) == (-5, False, 1.5, "AQI=", [1, "x"], {"k": "v"})
    assert a["trace_id"] == "0af7651916cd43dd8448eb211c80319c" and a["span_id"] == "b7ad6b7169203331"
    assert a["service.name"] == "s" and a["logger"] == "demo"
    assert b["timestamp"] == "2023-11-14T22:13:21.000000Z"   # observed time when time is unset
    assert b["event"] == "retry_scheduled" and b["level"] == "warning"
    assert b["log.record.original"] == "retrying in 2s"
    assert c["event"] == "cache_miss" and c["cache.key"] == "k1" and "timestamp" not in c
    assert d["log.record.original"] == "7" and "trace_id" not in d   # all-zero id = no trace


def test_traces_metrics_accepted_unknown_path_404_bad_body_400(rx):
    assert _post(rx, b"", path="/v1/traces")[0] == 200
    assert _post(rx, b"", path="/v1/metrics")[0] == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(rx, b"", path="/v1/nope")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(rx, b"\x0a\xff\xff", path="/v1/logs")
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(rx, b"[1]", "application/json")
    assert e.value.code == 400
    assert _lines(rx) == [] and rx.errors == 2


def test_claim3_gzip_and_deflate(rx):
    from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord
    import zlib
    body = _request(LogRecord(severity_number=9, body=AnyValue(string_value="a_done")))
    assert _post(rx, gzip.compress(body), encoding="gzip")[0] == 200
    assert _post(rx, zlib.compress(body), encoding="deflate")[0] == 200
    assert [x["event"] for x in _lines(rx)] == ["a_done", "a_done"]


# --- claim 3: capture -- cmd ---------------------------------------------------------------

@pytest.mark.weaver
def test_claim3_command_good_bad_failing(cli, tmp_path):
    out = tmp_path / "c.jsonl"
    good = cli("capture", "-o", out, "--", sys.executable, EMIT, "good", cwd=tmp_path)
    assert good.returncode == 0, good.stderr
    assert "captured 2 log records" in good.stderr and "0 violations" in good.stdout
    bad = cli("capture", "-o", out, "--json", "--", sys.executable, EMIT, "bad", cwd=tmp_path)
    assert bad.returncode == 1
    rules = {f["rule"] for f in json.loads(bad.stdout)["findings"]}   # parseable: the command's output went to stderr
    noisy = cli("capture", "-o", out, "--json", "--no-check", "--", sys.executable, "-c", "print('{not json')",
                cwd=tmp_path)
    assert noisy.stdout == "" and "{not json" in noisy.stderr
    assert rules == {"adr_banned_synonym", "adr_environment_value", "adr_event_name"}
    failing = cli("capture", "-o", out, "--", sys.executable, EMIT, "good", "7", cwd=tmp_path)
    assert failing.returncode == 7 and "command exited with 7" in failing.stderr
    gz = cli("capture", "-o", out, "--", sys.executable, EMIT, "good", cwd=tmp_path,
             env={"OTEL_EXPORTER_OTLP_COMPRESSION": "gzip"})
    assert gz.returncode == 0 and "captured 2 log records" in gz.stderr


def test_claim3_setup_errors(cli, tmp_path):
    missing = cli("capture", "-o", tmp_path / "x.jsonl", "--", "no-such-command-xyz", cwd=tmp_path)
    assert missing.returncode == 2 and "command not found" in missing.stderr
    nothing = cli("capture", "-o", tmp_path / "x.jsonl", "--", sys.executable, "-c", "pass", cwd=tmp_path)
    assert nothing.returncode == 2 and "no log records arrived" in nothing.stderr
    only = cli("capture", "--no-check", "-o", tmp_path / "x.jsonl", "--", sys.executable, "-c", "pass", cwd=tmp_path)
    assert only.returncode == 0


# --- claim 4: receive until a signal ---------------------------------------------------------

@pytest.mark.weaver
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_claim4_until_sigterm(tmp_path):
    out = tmp_path / "s.jsonl"
    p = subprocess.Popen([sys.executable, "-m", "logwarden", "capture", "-o", str(out), "--port", "0"],
                         cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first = p.stderr.readline()
    endpoint = first.split("receiving OTLP/HTTP logs on ")[1].split("/v1/logs")[0]
    env = {**os.environ, "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": endpoint + "/v1/logs"}
    subprocess.run([sys.executable, EMIT, "good"], env=env, check=True, timeout=60)
    time.sleep(0.3)
    p.send_signal(signal.SIGTERM)
    stdout, stderr = p.communicate(timeout=120)
    assert p.returncode == 0, stderr
    assert "captured 2 log records" in stderr and "0 violations" in stdout
    assert len(out.read_text().splitlines()) == 2


def test_chunked_request_body(rx):
    """Node's http client sends Transfer-Encoding: chunked without Content-Length."""
    import http.client
    doc = json.dumps({"resourceLogs": [{"scopeLogs": [{"logRecords": [
        {"severityNumber": 9, "body": {"stringValue": "chunk_done"}}]}]}]}).encode()
    conn = http.client.HTTPConnection(rx.host, rx.port, timeout=10)
    conn.putrequest("POST", "/v1/logs")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    for part in (doc[:10], doc[10:]):
        conn.send(f"{len(part):x}\r\n".encode() + part + b"\r\n")
    conn.send(b"0\r\n\r\n")
    assert conn.getresponse().status == 200
    assert [x["event"] for x in _lines(rx)] == ["chunk_done"]
