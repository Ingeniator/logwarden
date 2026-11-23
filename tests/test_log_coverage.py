"""v0.11.0: `logwarden log-coverage`: which log calls the tests run, and which wrote a line."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import run_cli

from logwarden import logcoverage as lc

FIXTURE = Path(__file__).parent / "data" / "logcov"


def _marks(path: Path, mark: str) -> set[int]:
    return {i for i, line in enumerate(path.read_text().splitlines(), 1) if f"# mark:{mark}" in line}


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """The fixture copied, run under coverage.py, with the three coverage formats and the
    captured lines."""
    d = tmp_path_factory.mktemp("logcov") / "proj"
    shutil.copytree(FIXTURE, d)
    env = {**os.environ, "LOGWARDEN_CALLSITE": "1", "COVERAGE_FILE": str(d / ".coverage")}
    env.pop("LOGWARDEN_CAPTURE", None)
    run = subprocess.run([sys.executable, "-m", "coverage", "run", "--source=app", "run.py"],
                         cwd=d, env=env, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    (d / "logs.jsonl").write_text(run.stdout + run.stderr)
    for fmt, out in (("json", "coverage.json"), ("xml", "coverage.xml")):
        r = subprocess.run([sys.executable, "-m", "coverage", fmt, "-q", "-o", out], cwd=d, env=env,
                           capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
    return d


def _where(items):
    return {(i["path"], i["line"]) for i in items}


# ---------------------------------------------------------------- claim 1

def test_claim1_finds_the_log_calls():
    calls, pragma = lc.find_calls([str(FIXTURE / "app")], cwd=FIXTURE)
    svc, unused = FIXTURE / "app/svc.py", FIXTURE / "app/unused.py"
    marked = {("app/svc.py", n) for m in ("reached-emitted", "reached-silent", "unreached")
              for n in _marks(svc, m)} | {("app/unused.py", n) for n in _marks(unused, "unreached")}
    assert {(c.path, c.line) for c in calls} == marked
    assert pragma == 1                                   # excluded() under # pragma: no cover
    by_line = {c.line: c for c in calls if c.path == "app/svc.py"}
    text = svc.read_text().splitlines()
    level_of = lambda word: next(c.level for n, c in by_line.items() if word in text[n - 1])
    assert level_of("svc_level_arg") == "warning"        # log.log(logging.WARNING, ...)
    assert level_of("svc_fail_failed") == "exception" and level_of("svc_root_failed") == "error"
    assert level_of("svc_never_failed") == "critical" and level_of("svc_debug") == "debug"
    inner = next(c for c in calls if "svc_inner" in text[c.line - 1])
    assert inner.stmt_line == inner.line - 2             # the multi-line `result = compute(` statement
    receivers = {c.receiver for c in calls}
    assert receivers == {"self._logger", "log", "structlog.get_logger().bind(step=1)", "logging"}


@pytest.mark.parametrize("recv,found", [
    ("log", True), ("logger", True), ("self._logger", True), ("app.logger", True), ("LOG", True),
    ("logging", True), ("structlog.get_logger()", True), ("slog", True), ("self.log", True),
    ("dialog", False), ("catalog", False), ("self", False), ("blog", False), ("logs_dir", False),
    ("warnings", False),
])
def test_claim1_receivers(tmp_path, recv, found):
    (tmp_path / "m.py").write_text(f"{recv}.info('x')\n")
    calls, _ = lc.find_calls([str(tmp_path)], cwd=tmp_path)
    assert bool(calls) is found


# ---------------------------------------------------------------- claim 2

def test_claim2_three_formats_agree(project):
    expect = {("app/svc.py", n) for n in _marks(project / "app/svc.py", "reached-emitted")
              | _marks(project / "app/svc.py", "reached-silent")}
    results = {}
    for name in (".coverage", "coverage.json", "coverage.xml"):
        r = lc.measure(["app"], coverage=project / name, cwd=project)
        results[name] = r
        reached = {(c["path"], c["line"]) for c in _all(project)} - _where(r["unreached"])
        assert reached == expect, name
        assert ("app/unused.py", min(_marks(project / "app/unused.py", "unreached"))) in _where(r["unreached"])
        assert r["excluded_by_pragma"] == 1 and r["calls"] == 10
    assert len({json.dumps({k: v for k, v in r.items()}, sort_keys=True) for r in results.values()}) == 1


def _all(project):
    calls, _ = lc.find_calls(["app"], cwd=project)
    return [{"path": c.path, "line": c.line} for c in calls]


def test_claim2_paths_recorded_elsewhere(project, tmp_path):
    """Coverage data from a CI checkout in another directory still matches by trailing path."""
    data = json.loads((project / "coverage.json").read_text())
    data["files"] = {f"/builds/team/proj/{k}": v for k, v in data["files"].items()}
    (tmp_path / "ci.json").write_text(json.dumps(data))
    here = lc.measure(["app"], coverage=project / "coverage.json", cwd=project)
    there = lc.measure(["app"], coverage=tmp_path / "ci.json", cwd=project)
    assert here["reached"] == there["reached"] == 7


# ---------------------------------------------------------------- claim 3

def test_claim3_emitted(project):
    r = lc.measure(["app"], coverage=project / ".coverage", logs=project / "logs.jsonl", cwd=project)
    svc = project / "app/svc.py"
    assert r["measure"] == "emitted" and r["reached"] == 7 and r["emitted"] == 6
    assert _where(r["not_emitted"]) - _where(r["unreached"]) == {("app/svc.py", n) for n in _marks(svc, "reached-silent")}
    alone = lc.measure(["app"], logs=project / "logs.jsonl", cwd=project)
    assert alone["emitted"] == 6 and alone["reached"] is None and alone["unreached"] is None


# ---------------------------------------------------------------- claim 5

def test_claim5_gate_and_levels(project):
    def cli(*args):
        return run_cli("log-coverage", "app", "--coverage", ".coverage", *args, cwd=project)
    assert cli().returncode == 0                                  # no gate by default
    assert cli("--fail-under", "70").returncode == 0              # 7/10
    r = cli("--fail-under", "71")
    assert r.returncode == 1 and "FAIL: 70% reached, required 71%" in r.stdout
    errs = json.loads(cli("--levels", "error,exception,critical", "--json").stdout)
    assert (errs["calls"], errs["reached"]) == (4, 2) and set(errs["by_level"]) == {"error", "exception", "critical"}
    assert cli("--levels", "error,exception,critical", "--fail-under", "50").returncode == 0
    assert cli("--levels", "error,exception,critical", "--fail-under", "51").returncode == 1


@pytest.mark.parametrize("args,msg", [
    ((), "give --coverage"),
    (("--coverage", "nope.json"), "no such coverage file"),
    (("--coverage", "run.py"), "is not coverage data"),
    (("--coverage", ".coverage", "--levels", "eror"), "unknown level eror"),
    (("--coverage", ".coverage", "--receiver", "("), "bad --receiver"),
    (("--logs", "nope.jsonl"), "no such log file"),
])
def test_claim5_usage_errors(project, args, msg):
    r = run_cli("log-coverage", "app", *args, cwd=project)
    assert r.returncode == 2 and msg in r.stderr, r.stderr


def test_claim4_outputs_agree(project, tmp_path):
    r = run_cli("log-coverage", "app", "--coverage", ".coverage", "--markdown", str(tmp_path / "s.md"), cwd=project)
    j = json.loads(run_cli("log-coverage", "app", "--coverage", ".coverage", "--json", cwd=project).stdout)
    md = (tmp_path / "s.md").read_text()
    assert "7 of 10 log calls reached" in r.stdout and j["reached"] == 7 and j["percent"] == 70.0
    assert "### Log coverage: 70% of 10 log calls reached" in md and "Not reached (3)" in md
    for u in j["unreached"]:
        assert f"{u['path']}:{u['line']}" in r.stdout and f"`{u['path']}:{u['line']}`" in md
    assert j["calls"] == sum(d["calls"] for d in j["by_level"].values()) == sum(f["calls"] for f in j["files"])
