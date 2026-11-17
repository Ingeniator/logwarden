"""v0.3.0 claim 2: exit codes 0 clean / 1 violations / 2 usage or setup error, plus check behaviour."""
import json

import pytest

from conftest import EXAMPLES

LINE = ('{"level":"info","event":"job_done","logger":"w","service.name":"s",'
        '"service.version":"1","deployment.environment.name":"test","job_id":"j1"}\n')
FIELDS = """groups:
  - id: registry.test.log
    type: attribute_group
    brief: test
    attributes:
      - id: job_id
        type: string
        stability: development
        brief: Job id.
        examples: ["j1"]
"""


@pytest.mark.weaver
def test_exit_0_clean(cli, tmp_path):
    assert cli("check", EXAMPLES / "good.jsonl", cwd=tmp_path).returncode == 0


@pytest.mark.weaver
def test_exit_1_violations(cli, tmp_path):
    assert cli("check", EXAMPLES / "bad.jsonl", cwd=tmp_path).returncode == 1


def test_exit_2_missing_file(cli, tmp_path):
    r = cli("check", tmp_path / "nope.jsonl", cwd=tmp_path)
    assert r.returncode == 2 and "no such file" in r.stderr


def test_exit_2_missing_fields_file(cli, tmp_path):
    r = cli("check", EXAMPLES / "good.jsonl", "--fields", tmp_path / "nope.yaml", cwd=tmp_path)
    assert r.returncode == 2 and "no such fields file" in r.stderr


@pytest.mark.weaver
def test_exit_2_invalid_fields_file(cli, tmp_path):
    bad = tmp_path / "f.yaml"
    bad.write_text("groups:\n  - id: x\n    type: not_a_type\n")
    r = cli("check", EXAMPLES / "good.jsonl", "--fields", bad, cwd=tmp_path)
    assert r.returncode == 2 and "weaver produced no report" in r.stderr


def test_exit_2_fields_and_registry(cli, tmp_path):
    f = tmp_path / "f.yaml"
    f.write_text(FIELDS)
    r = cli("check", EXAMPLES / "good.jsonl", "--fields", f, "--registry", tmp_path, cwd=tmp_path)
    assert r.returncode == 2


def test_exit_2_bad_usage(cli):
    assert cli("frobnicate").returncode == 2
    assert cli("check").returncode == 2


def test_rules(cli):
    r = cli("rules")
    assert r.returncode == 0 and "adr_event_name\t§4" in r.stdout
    r = cli("rules", "adr_event_name")
    assert r.returncode == 0 and "\nfix: " in r.stdout
    assert cli("rules", "no_such_rule").returncode == 2


def test_version(cli):
    from logwarden import __version__
    assert cli("--version").stdout.strip() == __version__
    assert cli("version").stdout.strip() == __version__


@pytest.mark.weaver
def test_bad_example_covers_every_rule_with_line_numbers(cli, tmp_path):
    res = json.loads(cli("check", EXAMPLES / "bad.jsonl", "--json", cwd=tmp_path).stdout)
    rules = {r["rule"] for r in res["by_rule"]}
    assert {"adr_missing_required", "adr_banned_synonym", "adr_semconv_prefix", "adr_environment_value",
            "adr_event_name", "adr_failure_context", "missing_attribute"} <= rules
    assert any(f["rule"] == "adr_failure_context" and f["line"] == 3 for f in res["findings"])
    assert res["skipped_lines"] == 1
    assert [f["rule"] for f in res["findings"] if f["field"] == "status"] == ["adr_banned_synonym"]
    assert all(r["hint"] for r in res["by_rule"])


@pytest.mark.weaver
def test_stdin_and_fields(cli, tmp_path):
    assert cli("check", "-", stdin=LINE, cwd=tmp_path).returncode == 1  # job_id unknown
    f = tmp_path / "f.yaml"
    f.write_text(FIELDS)
    assert cli("check", "-", "--fields", f, stdin=LINE, cwd=tmp_path).returncode == 0


@pytest.mark.weaver
def test_output_dir(cli, tmp_path):
    out = tmp_path / "out"
    cli("check", EXAMPLES / "bad.jsonl", "-o", out, cwd=tmp_path)
    assert {"samples.json", "live_check.json", "result.json"} <= {p.name for p in out.iterdir()}
