"""v0.10.1: logwarden's own messages pass its standard (claims 1, 2), and chunked Weaver runs
give the same findings (claim 3)."""
import json
import sys

import pytest

from conftest import EXAMPLES, run_cli
from logwarden import check

JSON = {"LOGWARDEN_LOG_FORMAT": "json"}


def py(code, env=None, cwd=None):
    import os
    import subprocess
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, **(env or {})}, cwd=cwd)


def records(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


@pytest.mark.weaver
def test_claim1_own_messages_pass_the_standard(tmp_path):
    (tmp_path / "logwarden.toml").write_text('nonsense_key = 1\n', encoding="utf-8")
    err = []
    runs = [
        (("check", EXAMPLES / "good.jsonl", "-o", tmp_path / "out"), 0),   # config warning, output written
        (("check", tmp_path / "missing.jsonl"), 2),
        (("check", "--profile", "nope", EXAMPLES / "good.jsonl"), 2),
        (("fetch", "--es", "http://127.0.0.1:9", "--index", "x"), 2),
        (("setup",), 0),
        (("capture", "--", sys.executable, "-c", "pass"), 2),
    ]
    for args, rc in runs:
        p = run_cli(*args, cwd=tmp_path, env=JSON)
        assert p.returncode == rc, (args, p.stderr)
        err.append(p.stderr)
    crash = py("import sys; from logwarden import cli\n"
               "def boom(a): raise RuntimeError('boom')\n"
               "cli.cmd_rules = boom\nsys.exit(cli.main(['rules']))", JSON)
    assert crash.returncode == 1
    err.append(crash.stderr)
    downloads = py("from logwarden import weaver\n"
                   "weaver._stderr('logwarden: downloading weaver v0 (x)...')\n"
                   "weaver._stderr_semconv('logwarden: downloading OTel semantic conventions v0...')", JSON)
    err.append(downloads.stderr)
    library = py("import logging\nfrom logwarden import selflog\nselflog.route_library_logs()\n"
                 "logging.getLogger('mcp.server').info('Processing request of type CallToolRequest')\n"
                 "logging.getLogger('mcp.server').warning('client went away')\n"
                 "try:\n    1 / 0\nexcept ZeroDivisionError:\n"
                 "    logging.getLogger('mcp').exception('handler failed')", JSON)
    err.append(library.stderr)

    own = tmp_path / "own.jsonl"
    own.write_text("".join(err), encoding="utf-8")
    recs = records(own.read_text(encoding="utf-8"))   # every line is a JSON object
    events = {r["event"] for r in recs}
    assert {"config_warning", "output_written", "command_failed", "semconv_ready", "capture_started",
            "capture_finished", "command_crashed", "weaver_download_started",
            "semconv_download_started", "library_log"} <= events, events
    crashed = next(r for r in recs if r["event"] == "command_crashed")
    assert crashed["exception.type"] == "builtins.RuntimeError" and "boom" in crashed["exception.stacktrace"]
    assert {r["error.type"] for r in recs if r["event"] == "command_failed"} >= {"usage_error", "invalid_arguments"}

    p = run_cli("check", own, "--profile", "standard", "--json", cwd=tmp_path / "out")
    result = json.loads(p.stdout)
    assert p.returncode == 0 and result["violations"] == 0, result.get("findings")


def test_claim1_text_mode_unchanged(tmp_path):
    p = run_cli("check", "missing.jsonl", cwd=tmp_path, env={"LOGWARDEN_LOG_FORMAT": ""})
    assert p.returncode == 2 and p.stderr == "logwarden: check: no such file: missing.jsonl\n"


@pytest.mark.parametrize("args", [
    ("--log-format", "json", "check", "missing.jsonl"),
    ("check", "missing.jsonl", "--log-format=json"),
    ("check", "--log-format", "json", "missing.jsonl"),
])
def test_claim1_log_format_option(tmp_path, args):
    p = run_cli(*args, cwd=tmp_path, env={"LOGWARDEN_LOG_FORMAT": "text"})
    (rec,) = records(p.stderr)
    assert p.returncode == 2 and rec["event"] == "command_failed" and rec["service.name"] == "logwarden"


def test_claim1_log_format_invalid_and_environment(tmp_path):
    p = run_cli("--log-format", "yaml", "version", cwd=tmp_path)
    assert p.returncode == 2 and "text or json" in p.stderr
    p = run_cli("check", "x", cwd=tmp_path, env={**JSON, "DEPLOYMENT_ENVIRONMENT": "",
                                                 "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment.name=staging"})
    assert records(p.stderr)[0]["deployment.environment.name"] == "staging"


@pytest.mark.weaver
def test_claim3_chunks_give_the_same_findings(monkeypatch, tmp_path):
    text = "".join((EXAMPLES / name).read_text(encoding="utf-8") for name in ("bad.jsonl", "good.jsonl")) * 3
    one, _, _ = check.evaluate(text)
    monkeypatch.setattr(check, "CHUNK_MIN", 4)
    monkeypatch.setattr(check, "_jobs", lambda n: 4)
    chunked, _, _ = check.evaluate(text, out=tmp_path)
    assert chunked == one and len(one) > 20
    report = json.loads((tmp_path / "live_check.json").read_text(encoding="utf-8"))
    samples = json.loads((tmp_path / "samples.json").read_text(encoding="utf-8"))
    # What Weaver saw: each distinct field once and each distinct line shape once.
    assert len(report["samples"]) == len(samples) < sum(len(json.loads(ln)) for ln in text.splitlines()
                                                         if ln.startswith("{"))
    assert {next(iter(x)) for x in samples} == {"attribute", "log"}
