"""lint-code without a working Semgrep: how it's found, and the errors when it can't run."""
import json
import subprocess

import pytest

from logwarden import lint


@pytest.fixture
def no_semgrep(monkeypatch, tmp_path):
    monkeypatch.delenv("SEMGREP", raising=False)
    monkeypatch.setattr(lint.shutil, "which", lambda name: None)
    monkeypatch.setattr(lint.sys, "executable", str(tmp_path / "python"))
    return tmp_path


def test_find_semgrep_order(no_semgrep, monkeypatch):
    with pytest.raises(lint.LintError):
        lint.find_semgrep()                                    # nothing anywhere
    (no_semgrep / "semgrep").write_text("")
    assert lint.find_semgrep() == [str(no_semgrep / "semgrep")]   # next to the interpreter (a venv)
    monkeypatch.setenv("SEMGREP", "/opt/semgrep")
    assert lint.find_semgrep() == ["/opt/semgrep"]             # $SEMGREP wins


def test_uvx_fallback(no_semgrep, monkeypatch):
    monkeypatch.setattr(lint.sys, "platform", "linux")
    monkeypatch.setattr(lint.shutil, "which", lambda name: "/bin/uvx" if name == "uvx" else None)
    assert lint.find_semgrep() == ["/bin/uvx", "--quiet", f"semgrep=={lint.SEMGREP_VERSION}"]


def _fake_run(monkeypatch, stdout="", stderr="", code=0, exc=None):
    def run(cmd, **kw):
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)
    monkeypatch.setenv("SEMGREP", "semgrep")
    monkeypatch.setattr(lint.subprocess, "run", run)


def test_semgrep_cannot_start(monkeypatch):
    _fake_run(monkeypatch, exc=FileNotFoundError("semgrep"))
    with pytest.raises(lint.LintError, match="can't run semgrep"):
        lint.run(["."])


def test_semgrep_crash_without_json(monkeypatch):
    _fake_run(monkeypatch, stderr="Traceback ...\nboom", code=2)
    with pytest.raises(lint.LintError, match=r"(?s)semgrep failed \(exit 2\).*boom"):
        lint.run(["."])


def test_semgrep_errors_without_results(monkeypatch):
    out = {"results": [], "errors": [{"level": "error", "message": "invalid rule"}]}
    _fake_run(monkeypatch, stdout=json.dumps(out), code=7)
    with pytest.raises(lint.LintError, match="semgrep: invalid rule"):
        lint.run(["."])


def _result(path, line, rule="logwarden-banned-key-python"):
    return {"check_id": f"x.{rule}", "path": path, "start": {"line": line},
            "extra": {"message": "§2: banned"}}


def test_findings_exclude_warnings_and_truncation(monkeypatch, tmp_path):
    src = tmp_path / "a.py"
    src.write_text("one\ntwo\n", encoding="utf-8")
    results = [_result(str(src), 2), _result(str(tmp_path / "gone.py"), 1),
               _result(str(src), 1, "logwarden-fstring-python")]
    out = {"results": results, "errors": [{"level": "warn", "message": "skipped"},
                                          {"message": "timeout on big.py\nmore"}]}
    _fake_run(monkeypatch, stdout=json.dumps(out), code=1)
    res = lint.run(["."], exclude=["logwarden-fstring-python"])
    assert [(f["line"], f["code"]) for f in res["findings"]] == [(2, "two"), (1, "")]  # gone.py: no code
    assert res["errors"] == ["timeout on big.py\nmore"] and not res["ok"]
    text = lint.text(res, limit=1)
    assert "1 more (--json for all)" in text and "semgrep warning: timeout on big.py" in text
    assert lint.text({"findings": [], "errors": []}).endswith("OK")
