"""v0.8.0 claim 1: SARIF from check and lint-code; callsite fields from configure()."""
import json
import os
import subprocess
import sys

import jsonschema
import pytest

from conftest import DATA, EXAMPLES, run_cli
from logwarden import __version__

SCHEMA = json.loads((DATA / "sarif-schema-2.1.0.json").read_text(encoding="utf-8"))
try:
    from logwarden import lint
    lint.find_semgrep()
    HAS_SEMGREP = True
except Exception:  # noqa: BLE001
    HAS_SEMGREP = False


def _valid(doc):
    jsonschema.validate(doc, SCHEMA, cls=jsonschema.Draft4Validator)


def _line(**extra):
    o = {"timestamp": "2026-11-14T10:00:00Z", "level": "info", "event": "order_created", "logger": "app",
         "service.name": "api", "service.version": "1.0.0", "deployment.environment.name": "production", **extra}
    return json.dumps(o) + "\n"


def test_claim1_check_sarif_points_at_the_source_line(tmp_path):
    root = tmp_path / "repo"
    (root / "src/app").mkdir(parents=True)
    logs = (_line(status=200, **{"code.file.path": "src/app/orders.py", "code.line.number": 42,
                                 "code.function.name": "app.orders.create"})
            + _line(http_route="/x", **{"code.file.path": str(root / "src/app/billing.py"), "code.line.number": 7})
            + _line(foo_bar=1, **{"code.file.path": "/build/svc/src/app/search.go", "code.line.number": 3})
            + _line(password="hunter2"))                                       # no location
    (root / "logs.jsonl").write_text(logs, encoding="utf-8")
    r = run_cli("check", "logs.jsonl", "--sarif", "out.sarif", cwd=root)
    assert r.returncode == 1, r.stderr
    doc = json.loads((root / "out.sarif").read_text(encoding="utf-8"))
    _valid(doc)
    run = doc["runs"][0]
    assert run["tool"]["driver"]["version"] == __version__
    where = {res["ruleId"]: res["locations"][0]["physicalLocation"] for res in run["results"]}
    assert where["adr_banned_synonym"] == {"artifactLocation": {"uri": "src/app/orders.py"},
                                           "region": {"startLine": 42}}
    assert where["adr_semconv_prefix"]["artifactLocation"]["uri"] == "src/app/billing.py"   # absolute, under cwd
    assert where["adr_sensitive_data"] == {"artifactLocation": {"uri": "logs.jsonl"}, "region": {"startLine": 4}}
    assert where["missing_attribute"]["artifactLocation"]["uri"] == "file:///build/svc/src/app/search.go"
    res = next(x for x in run["results"] if x["ruleId"] == "adr_banned_synonym")
    assert res["locations"][0]["logicalLocations"][0]["fullyQualifiedName"] == "app.orders.create"
    assert "log line 1" in res["message"]["text"]
    for rule in run["tool"]["driver"]["rules"]:
        assert rule["helpUri"].startswith("https://github.com/Ingeniator/logwarden/blob/")
        assert rule["helpUri"].endswith(f"docs/rules.md#{rule['id']}")
    assert "hunter2" not in json.dumps(doc)
    # --source-root strips a build path prefix, so the location matches the repository.
    r = run_cli("check", "logs.jsonl", "--sarif", "out2.sarif", "--source-root", "/build/svc", cwd=root)
    doc = json.loads((root / "out2.sarif").read_text(encoding="utf-8"))
    uris = {x["ruleId"]: x["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
            for x in doc["runs"][0]["results"]}
    assert uris["missing_attribute"] == "src/app/search.go"


def test_claim1_sarif_respects_baseline_and_is_empty_when_clean(tmp_path):
    (tmp_path / "good.jsonl").write_text((EXAMPLES / "good.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    assert run_cli("check", "good.jsonl", "--sarif", "g.sarif", cwd=tmp_path).returncode == 0
    doc = json.loads((tmp_path / "g.sarif").read_text(encoding="utf-8"))
    _valid(doc)
    assert doc["runs"][0]["results"] == []
    (tmp_path / "bad.jsonl").write_text((EXAMPLES / "bad.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    run_cli("baseline", "bad.jsonl", "--output", "b.json", cwd=tmp_path)
    run_cli("check", "bad.jsonl", "--baseline", "b.json", "--sarif", "b.sarif", cwd=tmp_path)
    assert json.loads((tmp_path / "b.sarif").read_text(encoding="utf-8"))["runs"][0]["results"] == []


@pytest.mark.skipif(not HAS_SEMGREP, reason="semgrep not available (no Windows build)")
def test_claim1_lint_code_sarif(tmp_path):
    src = tmp_path / "svc.py"
    src.write_text("import logging\nlog = logging.getLogger(__name__)\n"
                   "def f(n):\n    log.info(f'Loaded {n} models')\n", encoding="utf-8")
    r = run_cli("lint-code", "svc.py", "--sarif", "lint.sarif", cwd=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    doc = json.loads((tmp_path / "lint.sarif").read_text(encoding="utf-8"))
    _valid(doc)
    res = doc["runs"][0]["results"]
    assert res and res[0]["locations"][0]["physicalLocation"] == {"artifactLocation": {"uri": "svc.py"},
                                                                  "region": {"startLine": 4}}
    assert doc["runs"][0]["tool"]["driver"]["name"] == "logwarden lint-code"


_EMIT = """
import logging
from logwarden.logging import configure
log = configure(service_name="api", service_version="1.0.0", environment="test"{arg})
def create():
    log.info("order_created")
    logging.getLogger("lib").info("library line")
create()
"""


def _emit(tmp_path, arg="", env=None):
    script = tmp_path / "emit.py"
    script.write_text(_EMIT.format(arg=arg), encoding="utf-8")
    e = {k: v for k, v in os.environ.items() if k != "LOGWARDEN_CALLSITE"}
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8",
                         cwd=tmp_path, env={**e, **(env or {})}, check=True).stdout
    return [json.loads(ln) for ln in out.splitlines()]


def test_claim1_configure_callsite(tmp_path):
    lines = _emit(tmp_path, ", callsite=True")
    assert lines[0]["code.file.path"] == "emit.py" and lines[0]["code.line.number"] == 6
    assert lines[0]["code.function.name"] == "emit.create"
    assert lines[1]["code.line.number"] == 7                  # stdlib logger through the same setup
    (tmp_path / "logs.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    r = run_cli("check", "logs.jsonl", "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stdout                        # the fields are semconv: they pass
    assert "code.file.path" not in _emit(tmp_path)[0]         # off by default
    assert "code.file.path" in _emit(tmp_path, env={"LOGWARDEN_CALLSITE": "1"})[0]


def test_claim1_pytest_plugin_turns_callsite_on(pytester, monkeypatch, tmp_path):
    out = tmp_path / "cap.jsonl"
    monkeypatch.delenv("LOGWARDEN_CALLSITE", raising=False)
    monkeypatch.setenv("LOGWARDEN_CAPTURE", str(out))
    pytester.makepyfile(
        "from logwarden.logging import configure\n"
        "log = configure(service_name='api', service_version='1', environment='test')\n"
        "def test_x():\n"
        "    log.info('order_created')\n")
    pytester.runpytest_subprocess().assert_outcomes(passed=1)
    line = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert line["code.file.path"] == "test_claim1_pytest_plugin_turns_callsite_on.py"
    assert line["code.line.number"] == 4


def test_claim1_fingerprints_survive_moved_lines(tmp_path):
    def sarif(line_no, name):
        (tmp_path / f"{name}.jsonl").write_text(
            _line(status=200, **{"code.file.path": "app.py", "code.line.number": line_no,
                                 "code.function.name": "app.create"})
            + _line(status=500, **{"code.file.path": "app.py", "code.line.number": line_no + 5,
                                   "code.function.name": "app.create"}), encoding="utf-8")
        run_cli("check", f"{name}.jsonl", "--sarif", f"{name}.sarif", cwd=tmp_path)
        doc = json.loads((tmp_path / f"{name}.sarif").read_text(encoding="utf-8"))
        return [r["partialFingerprints"] for r in doc["runs"][0]["results"]]
    before, after = sarif(10, "before"), sarif(30, "after")
    assert before == after                                    # same alerts after the code moved
    assert before[0]["primaryLocationLineHash"] != before[1]["primaryLocationLineHash"]   # two calls, two alerts
