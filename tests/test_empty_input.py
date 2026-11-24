"""v0.12.0 bug fix: an empty log file (or only blank lines) is 0 records, not a crash."""
import json

import pytest


@pytest.mark.weaver
@pytest.mark.parametrize("content", ["", "\n\n"])
def test_empty_file(cli, tmp_path, content):
    f = tmp_path / "logs.jsonl"
    f.write_text(content)
    proc = cli("check", f, "--json", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    r = json.loads(proc.stdout)
    assert (r["ok"], r["records"], r["violations"]) == (True, 0, 0)
    assert "Traceback" not in proc.stderr
    text = cli("check", f, cwd=tmp_path)
    assert text.returncode == 0 and "0 records" in text.stdout


@pytest.mark.weaver
def test_empty_stdin_and_baseline(cli, tmp_path):
    proc = cli("check", "-", "--json", cwd=tmp_path, stdin="")
    assert proc.returncode == 0 and json.loads(proc.stdout)["records"] == 0, proc.stderr
    f = tmp_path / "logs.jsonl"
    f.write_text("")
    b = cli("baseline", f, "--output", tmp_path / "b.json", cwd=tmp_path)
    assert b.returncode == 0, b.stderr
