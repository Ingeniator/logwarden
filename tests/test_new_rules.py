"""v0.4.0 claims 5-8: adr_sensitive_data, adr_non_json_line, adr_trace_context, non-string values."""
import json

import pytest

from logwarden.check import parse

BASE = {"level": "info", "logger": "a", "service.name": "s", "service.version": "1",
        "deployment.environment.name": "test"}
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN = "00f067aa0ba902b7"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"

# event -> (extra fields, expected adr_sensitive_data fields)
SENSITIVE = {
    "k_password": ({"password": "hunter2"}, {"password"}),
    "k_secret": ({"client_secret": "abc"}, {"client_secret"}),
    "k_token": ({"csrf_token": "abc", "auth.token": "x"}, {"csrf_token", "auth.token"}),
    "k_api_key": ({"api-key": "abc", "apikey": "def"}, {"api-key", "apikey"}),
    "k_header": ({"authorization": "Basic Zm9vOmJhcg==", "cookie": "sid=1"}, {"authorization", "cookie"}),
    "k_number": ({"password": 123456}, {"password"}),
    "nested": ({"headers_dump": '{"Authorization":"x","host":"h"}'}, {"headers_dump"}),
    "jwt": ({"note": f"got {JWT}"}, {"note"}),
    "bearer": ({"note": "Authorization: Bearer abcdefghijklmnop1234"}, {"note"}),
    "openai": ({"note": "sk-proj-abcdefghijklmnopqrstuvwxyz012345"}, {"note"}),
    "github": ({"note": "ghp_" + "a" * 36}, {"note"}),
    "aws": ({"note": "AKIAIOSFODNN7EXAMPLE"}, {"note"}),
    "slack": ({"note": "xoxb-1234567890-abcdef"}, {"note"}),
    "google": ({"note": "AIza" + "B" * 35}, {"note"}),
    "pem": ({"note": "-----BEGIN RSA PRIVATE KEY-----"}, {"note"}),
    "dsn": ({"dsn": "postgres://app:s3cret@db:5432/x", "dsn2": "redis://:pw@cache:6379/0"}, {"dsn", "dsn2"}),
    "email": ({"note": "sent to bob.smith@example.co.uk"}, {"note"}),
    "card": ({"note": "card 4111-1111-1111-1111", "n": 5555555555554444}, {"note", "n"}),
    # negatives
    "redacted": ({"password": "***", "api_key": "[REDACTED]", "token": "<redacted>", "cookie": ""}, set()),
    "nested_redacted": ({"data": '{"password":"***","token":null}'}, set()),
    "counts": ({"gen_ai.usage.output_tokens": 12, "token_count": 3, "max_tokens": 9, "tokenizer": "bpe"}, set()),
    "flags": ({"password_set": True, "has_secret": False, "password": True}, set()),
    "ids": ({"order_id": 4111111111111112, "ts_ns": 1731888000000000000,
             "request_id": "550e8400-e29b-41d4-a716-446655440000", "phone_like": "2024-11-14 10:00:00"}, set()),
    "urls": ({"url.full": "https://api.example.com/v1/x?a=1", "sha": "deadbeef" * 5,
              "task": "task-abcdefghijklmnopqrstuvwxyz"}, set()),
}

SECRETS = ["hunter2", "Zm9vOmJhcg==", JWT, "abcdefghijklmnop1234", "s3cret", "bob.smith@example.co.uk",
           "4111-1111-1111-1111", "AKIAIOSFODNN7EXAMPLE"]


def _line(event, **extra):
    return json.dumps({**BASE, "event": event, **extra})


@pytest.fixture(scope="module")
def sensitive_result(tmp_path_factory):
    from conftest import run_cli
    text = "\n".join(_line(ev, **fields) for ev, (fields, _) in SENSITIVE.items()) + "\n"
    r = run_cli("check", "-", "--json", stdin=text, cwd=tmp_path_factory.mktemp("s"))
    return json.loads(r.stdout)


@pytest.mark.weaver
@pytest.mark.parametrize("event", SENSITIVE)
def test_sensitive_data(sensitive_result, event):
    got = {f["field"] for f in sensitive_result["findings"]
           if f["event"] == event and f["rule"] == "adr_sensitive_data"}
    assert got == SENSITIVE[event][1]


@pytest.mark.weaver
def test_sensitive_messages_never_contain_the_value(sensitive_result):
    out = json.dumps(sensitive_result, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in out


@pytest.mark.weaver
def test_secret_key_not_also_reported_as_unknown(sensitive_result):
    unknown = {(f["event"], f["field"]) for f in sensitive_result["findings"] if f["rule"] == "missing_attribute"}
    assert ("k_password", "password") not in unknown
    assert ("redacted", "password") in unknown  # redacted value: the field is still undeclared


def test_parse_non_json_lines():
    records, bad, total = parse('{"a":1}\nplain\n\n   \n[1]\n2\n"s"\nnull\n{"b":2}\n')
    assert [r.line for r in records] == [1, 9]
    assert [(b.line, b.kind) for b in bad] == [(2, "plain text"), (5, "JSON array"), (6, "JSON number"),
                                               (7, "JSON string"), (8, "JSON literal")]
    assert total == 9


@pytest.mark.weaver
def test_non_json_rule(cli, tmp_path):
    text = _line("a_done") + "\nINFO: started\n\n[1,2]\n" + _line("b_done") + "\n"
    res = json.loads(cli("check", "-", "--json", stdin=text, cwd=tmp_path).stdout)
    assert [(f["line"], f["message"]) for f in res["findings"]] == [
        (2, "§1: line is not a JSON object (plain text)"), (4, "§1: line is not a JSON object (JSON array)")]
    assert res["records"] == 2 and res["skipped_lines"] == 3
    assert "INFO: started" not in json.dumps(res)


TRACES = {
    "t_ok": ({"trace_id": TRACE, "span_id": SPAN}, set()),
    "t_only_trace": ({"trace_id": TRACE}, set()),
    "t_upper": ({"trace_id": TRACE.upper(), "span_id": SPAN}, {"trace_id"}),
    "t_short": ({"trace_id": TRACE[:16], "span_id": SPAN + "00"}, {"trace_id", "span_id"}),
    "t_zero": ({"trace_id": "0" * 32, "span_id": "0" * 16}, {"trace_id", "span_id"}),
    "t_nonhex": ({"trace_id": "g" * 32}, {"trace_id"}),
    "t_number": ({"trace_id": 123}, {"trace_id"}),
    "t_span_only": ({"span_id": SPAN}, {"span_id"}),
}


@pytest.mark.weaver
def test_trace_context(cli, tmp_path):
    text = "\n".join(_line(ev, **f) for ev, (f, _) in TRACES.items()) + "\n"
    res = json.loads(cli("check", "-", "--json", stdin=text, cwd=tmp_path).stdout)
    for ev, (_, want) in TRACES.items():
        got = {f["field"] for f in res["findings"] if f["event"] == ev and f["rule"] == "adr_trace_context"}
        assert got == want, ev


@pytest.mark.weaver
@pytest.mark.parametrize("value", [5, 1.5, {"a": 1}, [1], True])
def test_non_string_values_do_not_break_the_run(cli, tmp_path, value):
    text = (json.dumps({**BASE, "event": value}) + "\n"
            + json.dumps({**BASE, "event": "x_done", "trace_id": value, "span_id": value}) + "\n"
            + _line("Not snake case") + "\n")
    r = cli("check", "-", "--json", stdin=text, cwd=tmp_path)
    assert r.returncode == 1, r.stderr
    res = json.loads(r.stdout)
    assert res["records"] == 3
    assert {f["line"] for f in res["findings"] if f["rule"] == "adr_event_name"} >= {1, 3}
    assert {f["field"] for f in res["findings"] if f["line"] == 2 and f["rule"] == "adr_trace_context"} \
        == {"trace_id", "span_id"}
