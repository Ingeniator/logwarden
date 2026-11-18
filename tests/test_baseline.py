"""v0.4.0 claim 2: with a baseline only new findings fail; matching survives line moves and numbers."""
import json

import pytest

from logwarden.check import mask

from test_new_rules import _line


def test_mask():
    assert mask("Loaded 3 models") == mask("Loaded 412 models") == "Loaded # models"
    assert mask("job 550e8400-e29b-41d4-a716-446655440000 a1b2c3d4e5") == "job # #"
    assert mask("import_completed") == "import_completed"


OLD = "\n".join([_line("Loaded 3 models"), "plain text", _line("x_done", status=200)]) + "\n"


@pytest.fixture
def project(cli, tmp_path):
    (tmp_path / "logs.jsonl").write_text(OLD)
    r = cli("baseline", "logs.jsonl", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "baseline = \".logwarden-baseline.json\"" in r.stdout
    return tmp_path


@pytest.mark.weaver
def test_baseline_file(project):
    content = json.loads((project / ".logwarden-baseline.json").read_text())
    assert content["format"] == 1
    entries = {(e["rule"], e["field"], e["event"]) for e in content["entries"]}
    assert ("adr_event_name", "", "Loaded # models") in entries
    assert ("adr_banned_synonym", "status", "x_done") in entries
    non_json = [e for e in content["entries"] if e["rule"] == "adr_non_json_line"]
    assert len(non_json) == 1 and non_json[0]["event"].startswith("line:")
    assert "plain text" not in json.dumps(content)


@pytest.mark.weaver
def test_check_with_baseline(cli, project):
    (project / "pyproject.toml").write_text('[tool.logwarden]\nbaseline = ".logwarden-baseline.json"\n')
    r = cli("check", "logs.jsonl", "--json", cwd=project)
    res = json.loads(r.stdout)
    assert r.returncode == 0 and res["violations"] == 0
    assert res["baselined"] == 3 and res["baseline_fixed"] == 0
    assert res["baseline"] == ".logwarden-baseline.json"

    # Lines reordered, numbers changed: still known. One new finding: fails on just that.
    moved = "\n".join([_line("x_done", status=500), _line("Loaded 7 models"), "plain text",
                       _line("cache_warmed", http_route="/a")]) + "\n"
    (project / "moved.jsonl").write_text(moved)
    res = json.loads(cli("check", "moved.jsonl", "--json", cwd=project).stdout)
    assert [(f["line"], f["rule"]) for f in res["findings"]] == [(4, "adr_semconv_prefix")]
    assert res["baselined"] == 3

    # A known finding fixed -> reported as no longer occurring.
    (project / "fixed.jsonl").write_text(_line("Loaded 3 models") + "\n")
    res = json.loads(cli("check", "fixed.jsonl", "--json", cwd=project).stdout)
    assert res["ok"] and res["baseline_fixed"] == 2
    text = cli("check", "fixed.jsonl", cwd=project).stdout
    assert "2 entries no longer occur" in text and "1 more known in baseline" in text

    assert cli("check", "logs.jsonl", "--no-baseline", cwd=project).returncode == 1


@pytest.mark.weaver
def test_baseline_flag_and_missing_file(cli, project):
    assert cli("check", "logs.jsonl", "--baseline", ".logwarden-baseline.json", cwd=project).returncode == 0
    r = cli("check", "logs.jsonl", "--baseline", "nope.json", cwd=project)
    assert r.returncode == 2 and "logwarden baseline" in r.stderr
    (project / "logwarden.toml").write_text('baseline = "missing.json"\n')
    assert cli("check", "logs.jsonl", cwd=project).returncode == 2
    (project / "bad.json").write_text('{"entries": 1}')
    assert cli("check", "logs.jsonl", "--baseline", "bad.json", cwd=project).returncode == 2


@pytest.mark.weaver
def test_baseline_output_and_config_path(cli, project):
    (project / "logwarden.toml").write_text('baseline = "conf/bl.json"\n')
    (project / "conf").mkdir()
    assert cli("baseline", "logs.jsonl", cwd=project).returncode == 0
    assert (project / "conf/bl.json").is_file()
    assert cli("baseline", "logs.jsonl", "--output", "x.json", cwd=project).returncode == 0
    assert (project / "x.json").is_file()
    assert cli("check", "logs.jsonl", cwd=project).returncode == 0
