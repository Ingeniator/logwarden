"""v0.10.1 claim 3: the speed changes (literal guards in the Rego rules, each distinct field and
line shape checked once, parallel chunks) give the findings logwarden 0.10.0 gave."""
import json

import pytest

from conftest import GOLDEN
from logwarden import check

VALUES = [
    "alice@example.com", "x a.b@c.io y", "no-at-sign", "user@localhost",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "eyJshort", "Bearer abcdefghijklmnop1234", "BEARER abcdefghijklmnop1234", "bearer short",
    "sk-" + "a" * 24, "ghp_" + "b" * 36, "github_pat_" + "c" * 40, "glpat-" + "d" * 20, "AKIA" + "E" * 16,
    "xoxb-" + "1234567890", "AIza" + "f" * 35, "akia" + "e" * 16,
    "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN PRIVATE KEY-----",
    "postgres://user:pw@db:5432/x", "https://example.com/a@b", "https://h/x",
    "4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111", "4111111111111112",
    "5500 0000 0000 0004", "order 4111111111111111 paid", "1234567890123", "79927398713",
    '{"password": "hunter2"}', '{"password": "***"}', '{"api_key":"xyz"}', '{"token":"[REDACTED]"}',
    '{"a": 1}', "plain", "", "/orders/42", "9f1c2d3e-aaaa-bbbb-cccc-1234567890ab"]
KEYS = ["note", "password", "user_password", "client_secret", "api-key", "apikey", "csrf_token",
        "token", "gen_ai.usage.input_tokens", "token_count", "authorization", "Set-Cookie",
        "credentials", "credential", "private_key", "access_key", "keyboard", "monkey", "passport"]
NUMBERS = [4111111111111111, 4111111111111112, 12, 3.5, True, False, None]
BASE = {"timestamp": "2026-11-22T10:00:00Z", "level": "info", "event": "x_happened",
        "service.name": "s", "service.version": "1", "deployment.environment.name": "test", "logger": "a"}


def corpus() -> str:
    lines = [{**BASE, k: v} for k in KEYS for v in VALUES + NUMBERS]
    lines += [{**BASE, "event": v} for v in VALUES]
    return "".join(json.dumps(x) + "\n" for x in lines)


@pytest.mark.weaver
@pytest.mark.parametrize("chunked", [False, True])
def test_claim3_sensitive_corpus_same_as_0_10_0(monkeypatch, chunked):
    if chunked:
        monkeypatch.setattr(check, "_jobs", lambda n: 4)
    found, _, _ = check.evaluate(corpus())
    got = sorted(f'{f["line"]} {f["rule"]} {f["field"]} {f["renames"]}' for f in found)
    want = json.loads((GOLDEN / "sensitive-corpus.json").read_text(encoding="utf-8"))["findings"]
    assert got == want
