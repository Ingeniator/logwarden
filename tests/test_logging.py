"""v0.5.0 claims 1-5, 7, 8: logwarden.logging.configure(), error helpers, test helpers."""
import json
import logging
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("structlog")

from conftest import run_cli  # noqa: E402
from logwarden import logging as lwlog  # noqa: E402
from logwarden.testing import assert_failure_logged, assert_logs_clean, capture  # noqa: E402

from test_new_rules import SENSITIVE  # noqa: E402

FIELDS = """groups:
  - id: registry.app.log
    type: attribute_group
    brief: test app fields
    attributes:
      - id: rows_imported
        type: int
        stability: development
        brief: Rows imported.
        examples: [3]
"""

APP = textwrap.dedent('''
    import logging, structlog
    from logwarden.logging import configure
    log = configure("billing-api", "1.4.2", "test")
    structlog.contextvars.bind_contextvars(request_id="r1")
    log.info("import_completed", rows_imported=3)
    log.warning("quota_low", **{"error.type": "quota"})
    try:
        1 / 0
    except ZeroDivisionError:
        log.exception("sync_failed")
    lib = logging.getLogger("somelib")
    lib.info("Started server process [%d]", 42)
    lib.warning("Retrying request")
    try:
        raise KeyError("k")
    except KeyError:
        lib.error("Request failed", exc_info=True)
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/health", "1.1", 200)
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/v1/x", "1.1", 200)
    log.info("probe_served", **{"http.route": "/metrics"})
    logging.getLogger("httpx").info("HTTP Request: GET https://x")
    print(end="", flush=True)
''')


def _run_app(tmp_path, code=APP, env=None):
    import os
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                       cwd=tmp_path, env={**os.environ, **(env or {})})
    return r


@pytest.fixture(scope="module")
def app_output(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("app")
    r = _run_app(tmp)
    assert r.returncode == 0, r.stderr
    return tmp, r.stdout


def test_app_writes_one_json_object_per_line(app_output):
    _, out = app_output
    recs = [json.loads(line) for line in out.splitlines()]
    events = [r["event"] for r in recs]
    assert events == ["import_completed", "quota_low", "sync_failed", "library_log", "library_log",
                      "library_log", "library_log"]
    for r in recs:
        assert r["service.name"] == "billing-api" and r["service.version"] == "1.4.2"
        assert r["deployment.environment.name"] == "test" and r["request_id"] == "r1"
        assert r["timestamp"].endswith("Z") and r["level"] in ("info", "warning", "error")
    lib = [r for r in recs if r["event"] == "library_log"]
    assert [r["log.record.original"] for r in lib][:3] == ["Started server process [42]", "Retrying request",
                                                           "Request failed"]
    assert "/v1/x" in lib[3]["log.record.original"]


@pytest.mark.weaver
def test_claim1_configure_output_passes_check(app_output):
    tmp, out = app_output
    (tmp / "logs.jsonl").write_text(out, encoding="utf-8")
    (tmp / "logwarden-fields.yaml").write_text(FIELDS)
    r = run_cli("check", "logs.jsonl", "--json", cwd=tmp)
    res = json.loads(r.stdout)
    assert res["violations"] == 0, res["findings"]


def test_claim3_exceptions(app_output):
    _, out = app_output
    recs = [json.loads(line) for line in out.splitlines()]
    sync = next(r for r in recs if r["event"] == "sync_failed")
    assert sync["exception.type"] == "ZeroDivisionError"
    assert sync["exception.message"] == "division by zero"
    assert "Traceback" in sync["exception.stacktrace"] and "exc_info" not in sync
    lib_err = next(r for r in recs if r.get("log.record.original") == "Request failed")
    assert lib_err["exception.type"] == "KeyError"


@pytest.mark.weaver
def test_claim3_trace_context(tmp_path):
    pytest.importorskip("opentelemetry.sdk")
    code = textwrap.dedent('''
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        trace.set_tracer_provider(TracerProvider())
        from logwarden.logging import configure
        log = configure("s", "1", "test")
        log.info("outside_span")
        with trace.get_tracer("t").start_as_current_span("work"):
            log.info("inside_span")
    ''')
    r = _run_app(tmp_path, code)
    recs = [json.loads(line) for line in r.stdout.splitlines()]
    assert "trace_id" not in recs[0] and "span_id" not in recs[0]
    assert len(recs[1]["trace_id"]) == 32 and len(recs[1]["span_id"]) == 16
    (tmp_path / "l.jsonl").write_text(r.stdout)
    res = json.loads(run_cli("check", "l.jsonl", "--json", cwd=tmp_path).stdout)
    assert res["ok"], res["findings"]


def test_claim4_noise(app_output):
    _, out = app_output
    assert "/health" not in out and "probe_served" not in out and "HTTP Request" not in out


def test_claim4_quiet_levels_in_process():
    import io
    lwlog.configure("s", "1", "test", stream=io.StringIO())
    for name in ("urllib3", "httpx", "httpcore", "botocore"):
        assert logging.getLogger(name).level == logging.WARNING


@pytest.mark.weaver
def test_claim2_redaction_leaves_nothing_for_the_rule(tmp_path):
    lines = []
    for event, (fields, _) in SENSITIVE.items():
        lines.append(f"log.info({event!r}, **{fields!r})")
    code = "from logwarden.logging import configure\nlog = configure('s', '1', 'test')\n" + "\n".join(lines) + "\n"
    r = _run_app(tmp_path, code)
    assert r.returncode == 0, r.stderr
    (tmp_path / "l.jsonl").write_text(r.stdout)
    res = json.loads(run_cli("check", "l.jsonl", "--json", cwd=tmp_path).stdout)
    assert [f for f in res["findings"] if f["rule"] == "adr_sensitive_data"] == []
    assert len(r.stdout.splitlines()) == len(SENSITIVE)


def test_claim2_keep(tmp_path):
    code = ("from logwarden.logging import configure\n"
            "log = configure('s', '1', 'test', keep=['user.email'])\n"
            "log.info('user_invited', **{'user.email': 'a@example.com', 'note': 'b@example.com'})\n")
    rec = json.loads(_run_app(tmp_path, code).stdout)
    assert rec["user.email"] == "a@example.com" and rec["note"] == "[REDACTED]"


def test_claim5_env_fallbacks_and_validation(tmp_path, monkeypatch):
    import io
    code = "from logwarden.logging import configure\nconfigure().info('started')\n"
    rec = json.loads(_run_app(tmp_path, code, env={
        "OTEL_SERVICE_NAME": "envsvc", "SERVICE_VERSION": "9", "DEPLOYMENT_ENVIRONMENT": "staging"}).stdout)
    assert (rec["service.name"], rec["service.version"], rec["deployment.environment.name"]) == ("envsvc", "9", "staging")
    rec = json.loads(_run_app(tmp_path, code, env={"OTEL_RESOURCE_ATTRIBUTES":
        "service.name=ra, service.version=2,deployment.environment.name=production"}).stdout)
    assert (rec["service.name"], rec["service.version"], rec["deployment.environment.name"]) == ("ra", "2", "production")

    for k in ("OTEL_SERVICE_NAME", "SERVICE_VERSION", "DEPLOYMENT_ENVIRONMENT", "OTEL_RESOURCE_ATTRIBUTES"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError, match="service_version"):
        lwlog.configure("s", None, "test", stream=io.StringIO())
    with pytest.raises(ValueError, match="must be one of"):
        lwlog.configure("s", "1", "dev", stream=io.StringIO())
    lwlog.configure("s", "1", "test", stream=io.StringIO())
    lwlog.configure("s", "1", "test", stream=io.StringIO())
    marked = [h for h in logging.getLogger().handlers if getattr(h, "_logwarden_handler", False)]
    assert len(marked) == 1


def test_console_mode(tmp_path):
    code = "from logwarden.logging import configure\nconfigure('s','1','development', json=False).info('started', a=1)\n"
    out = _run_app(tmp_path, code).stdout
    assert "started" in out and not out.lstrip().startswith("{")


# ---- claim 7: error helpers

class _Resp:
    def __init__(self, body):
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


@pytest.mark.parametrize("body, code, message", [
    ({"error": {"message": "Rate limit reached", "type": "requests", "code": "rate_limit_exceeded"}},
     "rate_limit_exceeded", "Rate limit reached"),
    ({"error": {"message": "Bad model", "type": "invalid_request_error", "code": None}},
     "invalid_request_error", "Bad model"),
    ({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
     "overloaded_error", "Overloaded"),
    ({"detail": "Not authenticated"}, None, "Not authenticated"),
    ({"detail": [{"loc": ["body", "x"], "msg": "field required"}]}, None,
     '[{"loc": ["body", "x"], "msg": "field required"}]'),
    ({"code": 4001, "message": "quota"}, "4001", "quota"),
    ({"error": "upstream_timeout"}, "upstream_timeout", "upstream_timeout"),
    ({"error": "Something broke badly"}, None, "Something broke badly"),
    ("<html>502 Bad Gateway</html>", None, "<html>502 Bad Gateway</html>"),
])
def test_claim7_extract(body, code, message):
    for given in (body, _Resp(body)):
        assert lwlog.extract_error_code(given) == code
        assert lwlog.extract_error_message(given) == message


def test_claim7_real_response_objects():
    httpx = pytest.importorskip("httpx")
    requests = pytest.importorskip("requests")
    body = {"error": {"message": "key sk-proj-abcdefghijklmnopqrstuvwxyz0123 invalid", "code": "invalid_api_key"}}
    r = httpx.Response(401, json=body)
    assert lwlog.extract_error_code(r) == "invalid_api_key"
    assert lwlog.extract_error_message(r) == "key [REDACTED] invalid"
    rr = requests.models.Response()
    rr.status_code, rr._content = 500, b"plain failure text"
    assert lwlog.extract_error_message(rr) == "plain failure text"
    assert lwlog.extract_error_code(rr) is None


def test_claim7_scrub_limits_and_redacts():
    long = "x" * 2000
    assert len(lwlog.extract_error_message({"message": long})) == 500
    assert len(lwlog.scrub(long, 100)) == 100
    s = lwlog.scrub("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_X "
                    "for bob@example.com card 4111 1111 1111 1111 db postgres://u:pw@h/x")
    assert "eyJ" not in s and "bob@" not in s and "4111" not in s and "pw@" not in s
    assert "postgres://[REDACTED]@h/x" in s


# ---- claim 8: test helpers

def _configured_logger():
    import io
    return lwlog.configure("s", "1", "test", stream=io.StringIO())


def test_claim8_capture_and_assert_failure_logged():
    log = _configured_logger()
    with capture() as logs:
        log.info("call_started")
        try:
            raise TimeoutError("slow")
        except TimeoutError:
            log.error("upstream_call_failed", exc_info=True, **{"server.address": "api.example.com"})
        log.error("other_failed")
    assert [r["event"] for r in logs] == ["call_started", "upstream_call_failed", "other_failed"]
    rec = assert_failure_logged(logs, "upstream_call_failed", **{"server.address": "api.example.com"})
    assert rec["exception.type"] == "TimeoutError"
    with pytest.raises(AssertionError, match="no failure line logged for event='missing_failed'"):
        assert_failure_logged(logs, "missing_failed")
    with pytest.raises(AssertionError, match="doesn't say what failed"):
        assert_failure_logged(logs, "other_failed")


@pytest.mark.weaver
def test_claim8_assert_logs_clean(tmp_path):
    log = _configured_logger()
    with capture() as logs:
        log.info("cache_warmed")
    assert assert_logs_clean(logs, cwd=tmp_path)["ok"]
    with capture() as bad:
        log.info("Loaded 3 models")
    with pytest.raises(AssertionError, match="adr_event_name"):
        assert_logs_clean(bad, cwd=tmp_path)
