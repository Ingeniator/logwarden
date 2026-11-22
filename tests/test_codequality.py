"""v0.8.1 claim 1: GitLab Code Quality report from check and lint-code."""
import json

import pytest

from conftest import EXAMPLES, run_cli

try:
    from logwarden import lint
    lint.find_semgrep()
    HAS_SEMGREP = True
except Exception:  # noqa: BLE001
    HAS_SEMGREP = False

SEVERITIES = {"info", "minor", "major", "critical", "blocker"}


def _line(**extra):
    o = {"timestamp": "2026-11-14T10:00:00Z", "level": "info", "event": "order_created", "logger": "app",
         "service.name": "api", "service.version": "1.0.0", "deployment.environment.name": "production", **extra}
    return json.dumps(o) + "\n"


def _read(path):
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")                 # GitLab rejects a BOM
    doc = json.loads(raw.decode("utf-8"))
    assert isinstance(doc, list)
    for e in doc:                                              # the fields GitLab requires
        assert isinstance(e["description"], str) and e["description"]
        assert isinstance(e["check_name"], str) and e["check_name"]
        assert isinstance(e["fingerprint"], str) and e["fingerprint"]
        assert e["severity"] in SEVERITIES
        p = e["location"]["path"]
        assert p and not p.startswith(("./", "/", "file:")) and "\\" not in p
        assert isinstance(e["location"]["lines"]["begin"], int) and e["location"]["lines"]["begin"] >= 1
    assert len({e["fingerprint"] for e in doc}) == len(doc)   # GitLab merges identical fingerprints
    return doc


def test_claim1_check_codequality(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    logs = (_line(status=200, **{"code.file.path": "src/app/orders.py", "code.line.number": 42})
            + _line(status=201, **{"code.file.path": str(root / "src/app/billing.py"), "code.line.number": 7})
            + _line(foo_bar=1, **{"code.file.path": "/build/svc/src/app/search.go", "code.line.number": 3})
            + _line(password="hunter2"))
    (root / "logs.jsonl").write_text(logs, encoding="utf-8")
    r = run_cli("check", "logs.jsonl", "--codequality", "gl-code-quality.json", cwd=root)
    assert r.returncode == 1, r.stderr
    doc = _read(root / "gl-code-quality.json")
    where = {(e["check_name"], e["location"]["path"], e["location"]["lines"]["begin"]) for e in doc}
    assert ("adr_banned_synonym", "src/app/orders.py", 42) in where
    assert ("adr_banned_synonym", "src/app/billing.py", 7) in where         # absolute, inside the repo
    assert ("adr_sensitive_data", "logs.jsonl", 4) in where                 # no source location: log line
    outside = next(e for e in doc if e["check_name"] == "missing_attribute")
    assert outside["location"] == {"path": "logs.jsonl", "lines": {"begin": 3}}   # outside the repo
    assert "logged at /build/svc/src/app/search.go:3" in outside["description"]
    assert {e["severity"] for e in doc if e["check_name"] == "adr_sensitive_data"} == {"critical"}
    assert {e["severity"] for e in doc if e["check_name"] != "adr_sensitive_data"} == {"major"}
    assert "hunter2" not in json.dumps(doc)
    # --source-root maps the build path into the repository.
    run_cli("check", "logs.jsonl", "--codequality", "cq2.json", "--source-root", "/build/svc", cwd=root)
    doc = _read(root / "cq2.json")
    assert next(e for e in doc if e["check_name"] == "missing_attribute")["location"] == \
        {"path": "src/app/search.go", "lines": {"begin": 3}}


def test_claim1_fingerprints_follow_moved_code(tmp_path):
    def report(line_no, name):
        (tmp_path / f"{name}.jsonl").write_text(
            _line(status=200, **{"code.file.path": "app.py", "code.line.number": line_no})
            + _line(status=500, **{"code.file.path": "app.py", "code.line.number": line_no + 5}), encoding="utf-8")
        run_cli("check", f"{name}.jsonl", "--codequality", f"{name}.json", cwd=tmp_path)
        return [e["fingerprint"] for e in _read(tmp_path / f"{name}.json")]
    before, after = report(10, "before"), report(30, "after")
    assert before == after and len(set(before)) == 2


def test_claim1_same_as_sarif_identity(tmp_path):
    """The same findings are the same across both formats: one entry per SARIF result."""
    (tmp_path / "bad.jsonl").write_text((EXAMPLES / "bad.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    run_cli("check", "bad.jsonl", "--codequality", "cq.json", "--sarif", "s.sarif", cwd=tmp_path)
    cq = _read(tmp_path / "cq.json")
    sarif = json.loads((tmp_path / "s.sarif").read_text(encoding="utf-8"))["runs"][0]["results"]
    assert len(cq) == len(sarif) == 10
    assert sorted(e["check_name"] for e in cq) == sorted(r["ruleId"] for r in sarif)


def test_claim1_baseline_disable_and_clean(tmp_path):
    (tmp_path / "good.jsonl").write_text((EXAMPLES / "good.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    assert run_cli("check", "good.jsonl", "--codequality", "g.json", cwd=tmp_path).returncode == 0
    assert _read(tmp_path / "g.json") == []
    (tmp_path / "bad.jsonl").write_text((EXAMPLES / "bad.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    run_cli("baseline", "bad.jsonl", "--output", "b.json", cwd=tmp_path)
    run_cli("check", "bad.jsonl", "--baseline", "b.json", "--codequality", "b-cq.json", cwd=tmp_path)
    assert _read(tmp_path / "b-cq.json") == []
    (tmp_path / "logwarden.toml").write_text('disable = { adr_sensitive_data = "test" }\n', encoding="utf-8")
    run_cli("check", "bad.jsonl", "--codequality", "d.json", cwd=tmp_path)
    assert "adr_sensitive_data" not in {e["check_name"] for e in _read(tmp_path / "d.json")}


def test_claim1_stdin_has_a_path(tmp_path):
    run_cli("check", "-", "--codequality", "cq.json", stdin=_line(status=200), cwd=tmp_path)
    assert {e["location"]["path"] for e in _read(tmp_path / "cq.json")} == {"logs"}


@pytest.mark.skipif(not HAS_SEMGREP, reason="semgrep not available (no Windows build)")
def test_claim1_lint_code_codequality(tmp_path):
    (tmp_path / "svc.py").write_text("import logging\nlog = logging.getLogger(__name__)\n"
                                     "def f(n):\n    log.info(f'Loaded {n} models')\n", encoding="utf-8")
    assert run_cli("lint-code", "svc.py", "--codequality", "cq.json", cwd=tmp_path).returncode == 1
    doc = _read(tmp_path / "cq.json")
    assert doc and doc[0]["location"] == {"path": "svc.py", "lines": {"begin": 4}}


def test_claim2_gitlab_template_shape():
    """The template reports without blocking, never uploads Weaver's work files, and runs offline."""
    yaml = pytest.importorskip("yaml")
    from conftest import ROOT
    job = yaml.safe_load((ROOT / "ci/gitlab/logwarden.gitlab-ci.yml").read_text(encoding="utf-8"))[".logwarden"]
    assert job["image"]["entrypoint"] == [""]
    assert job["allow_failure"] == {"exit_codes": [1]}            # exit 2 (broken setup) still fails
    assert job["artifacts"]["reports"]["codequality"] == "gl-code-quality.json"
    assert job["artifacts"]["when"] == "always"
    script = "\n".join(job["script"])
    assert "--codequality gl-code-quality.json" in script
    assert " -o " not in script and "--output" not in script    # -o would upload samples.json (logged values)
    assert "pip install" not in script and "uvx" not in script   # nothing downloaded at run time
