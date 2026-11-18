"""v0.5.0 claim 9: adr_upstream_failure_coverage."""
import json

import pytest

from test_new_rules import _line

UP = 'upstreams = ["api.openai.com", "api.anthropic.com"]\n'


@pytest.mark.weaver
def test_uncovered_upstream_fails_with_null_line(cli, tmp_path):
    (tmp_path / "logwarden.toml").write_text(UP)
    text = "\n".join([
        json.dumps({**json.loads(_line("upstream_call_failed")), "level": "error",
                    "server.address": "api.openai.com", "error.type": "timeout"}),
        _line("call_done", **{"server.address": "api.anthropic.com"}),   # success only
    ]) + "\n"
    r = cli("check", "-", "--json", stdin=text, cwd=tmp_path)
    res = json.loads(r.stdout)
    ups = [f for f in res["findings"] if f["rule"] == "adr_upstream_failure_coverage"]
    assert r.returncode == 1 and len(ups) == 1
    assert ups[0]["line"] is None and "api.anthropic.com" in ups[0]["message"]
    assert "  run\tadr_upstream_failure_coverage\t" in cli("check", "-", stdin=text, cwd=tmp_path).stdout

    # Covered by a *_failed event at info level too.
    text2 = text + _line("call_failed", **{"server.address": "api.anthropic.com", "error.type": "x"}) + "\n"
    assert cli("check", "-", stdin=text2, cwd=tmp_path).returncode == 0


@pytest.mark.weaver
def test_upstream_baseline_and_disable(cli, tmp_path):
    (tmp_path / "logwarden.toml").write_text(UP)
    (tmp_path / "l.jsonl").write_text(_line("ok_done") + "\n")
    assert cli("baseline", "l.jsonl", cwd=tmp_path).returncode == 0
    entries = json.loads((tmp_path / ".logwarden-baseline.json").read_text())["entries"]
    assert {e["event"] for e in entries if e["rule"] == "adr_upstream_failure_coverage"} == \
        {"api.openai.com", "api.anthropic.com"}
    assert cli("check", "l.jsonl", "--baseline", ".logwarden-baseline.json", cwd=tmp_path).returncode == 0
    (tmp_path / "logwarden.toml").write_text(UP + 'disable = { adr_upstream_failure_coverage = "no tests yet" }\n')
    assert cli("check", "l.jsonl", cwd=tmp_path).returncode == 0


@pytest.mark.parametrize("value", ['"api.openai.com"', '[1]', '[""]'])
def test_upstreams_must_be_list_of_hosts(cli, tmp_path, value):
    (tmp_path / "logwarden.toml").write_text(f"upstreams = {value}\n")
    r = cli("check", "-", stdin=_line("x_done") + "\n", cwd=tmp_path)
    assert r.returncode == 2 and "must be a list of host names" in r.stderr
