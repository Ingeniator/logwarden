"""v0.9.0: rule profiles (claim 1) and ranked fixes (claim 2)."""
import json

import pytest

from conftest import EXAMPLES, run_cli
from logwarden import check, report
from test_new_rules import _line

ESSENTIAL = {"adr_sensitive_data", "adr_non_json_line", "adr_missing_required"}


def _check(tmp_path, *args, text=None, name="bad.jsonl"):
    if text is not None:
        (tmp_path / name).write_text(text, encoding="utf-8")
    elif not (tmp_path / name).exists():
        (tmp_path / name).write_text((EXAMPLES / "bad.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    r = run_cli("check", name, "--json", *args, cwd=tmp_path)
    assert r.returncode in (0, 1), r.stderr
    return json.loads(r.stdout)


# ---------------------------------------------------------------- claim 1: profiles

def test_claim1_essential_checks_three_rules(tmp_path):
    std = _check(tmp_path)
    ess = _check(tmp_path, "--profile", "essential")
    assert std["profile"] == "standard" and std["outside_profile"] == 0
    assert {r["rule"] for r in ess["by_rule"]} == ESSENTIAL
    assert ess["profile"] == "essential"
    assert ess["violations"] + ess["outside_profile"] == std["violations"]
    assert {f["rule"] for f in ess["findings"]} <= ESSENTIAL


def test_claim1_config_and_option(tmp_path):
    (tmp_path / "logwarden.toml").write_text('profile = "essential"\n', encoding="utf-8")
    assert _check(tmp_path)["profile"] == "essential"
    assert _check(tmp_path, "--profile", "standard")["profile"] == "standard"   # the option wins
    (tmp_path / "logwarden.toml").write_text('profile = "strict"\n', encoding="utf-8")
    r = run_cli("check", "bad.jsonl", cwd=tmp_path)
    assert r.returncode == 2 and "profile must be one of essential, standard" in r.stderr
    assert run_cli("check", "bad.jsonl", "--profile", "x", cwd=tmp_path).returncode == 2


def test_claim1_disable_and_baseline_under_both(tmp_path):
    (tmp_path / "logwarden.toml").write_text('disable = { adr_sensitive_data = "test" }\n', encoding="utf-8")
    ess = _check(tmp_path, "--profile", "essential")
    assert {r["rule"] for r in ess["by_rule"]} == ESSENTIAL - {"adr_sensitive_data"}
    assert [d["rule"] for d in ess["disabled"]] == ["adr_sensitive_data"]
    (tmp_path / "logwarden.toml").unlink()
    # A baseline written under standard: essential runs clean and reports nothing as "fixed"
    # for the rules it doesn't check.
    assert run_cli("baseline", "bad.jsonl", "--output", "b.json", cwd=tmp_path).returncode == 0
    for prof in ("essential", "standard"):
        r = _check(tmp_path, "--profile", prof, "--baseline", "b.json")
        assert r["violations"] == 0 and r["baseline_fixed"] == 0, prof
    # A baseline written under essential leaves the other rules failing under standard.
    run_cli("baseline", "bad.jsonl", "--output", "e.json", "--profile", "essential", cwd=tmp_path)
    entries = json.loads((tmp_path / "e.json").read_text(encoding="utf-8"))["entries"]
    assert {e["rule"] for e in entries} == ESSENTIAL
    r = _check(tmp_path, "--profile", "standard", "--baseline", "e.json")
    assert r["violations"] > 0 and not ({x["rule"] for x in r["by_rule"]} & ESSENTIAL)


def test_claim1_compare_respects_profile(tmp_path):
    (tmp_path / "bad.jsonl").write_text((EXAMPLES / "bad.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    run_cli("check", "bad.jsonl", "--snapshot", "s.json", cwd=tmp_path)
    (tmp_path / "good.jsonl").write_text((EXAMPLES / "good.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    r = run_cli("check", "good.jsonl", "--json", "--profile", "essential", "--compare", "s.json", cwd=tmp_path)
    fixed = json.loads(r.stdout)["compare"]["fixed_entries"]
    assert fixed and {e["rule"] for e in fixed} <= ESSENTIAL


def test_claim1_text_and_reports_say_what_standard_adds(tmp_path):
    (tmp_path / "bad.jsonl").write_text((EXAMPLES / "bad.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    r = run_cli("check", "bad.jsonl", "--profile", "essential", "--markdown", "r.md", "--html", "r.html",
                cwd=tmp_path)
    assert "Profile essential: 7 more violations under the standard profile" in r.stdout
    assert "7 more violations" in (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "7 more violations" in (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "Profile" not in run_cli("check", "bad.jsonl", cwd=tmp_path).stdout


# ---------------------------------------------------------------- claim 2: ranked fixes

def _v(line, rule, *, logger="app", event="e", field=None, loc=None):
    return {"line": line, "rule": rule, "logger": logger, "fp_event": event, "field": field, "loc": loc,
            "level": "violation"}


def test_claim2_groups_and_order():
    vs = ([_v(i, "adr_banned_synonym", field="status", loc=("app/orders.py", 42, None)) for i in range(1, 6)]
          + [_v(i, "missing_attribute", field="foo", loc=("app/orders.py", 42, None)) for i in range(1, 6)]
          + [_v(i, "adr_event_name", logger="worker", event="Loaded # models") for i in range(6, 9)]
          + [_v(9, check.NON_JSON, logger=""), _v(10, check.NON_JSON, logger="")]
          + [_v(11, "adr_banned_synonym", field="status", loc=("app/pay.py", 7, None))]
          + [_v(None, "adr_upstream_failure_coverage")])
    groups = check.fix_first(vs)
    assert [(g["kind"], g["where"], g["lines"]) for g in groups] == [
        ("code", "app/orders.py:42", 5), ("logger", None, 3), ("non_json", None, 2), ("code", "app/pay.py:7", 1)]
    assert groups[0]["rules"] == {"adr_banned_synonym": 5, "missing_attribute": 5}
    assert groups[0]["fields"] == ["foo", "status"]
    assert groups[1]["logger"] == "worker" and groups[1]["event"] == "Loaded # models"
    assert sum(g["lines"] for g in groups) == 11                     # every line in exactly one group
    assert abs(sum(g["share"] for g in groups) - 100) < 0.5
    res = {"fix_first": groups, "lines_with_violations": 11}
    assert report.fix_first_head(res) == "the top 4 places cause 100% of the lines with violations"
    assert report.fix_first_head(res, top=3) == "the top 3 places cause 91% of the lines with violations"
    assert len(check.fix_first(vs, limit=2)) == 2


@pytest.mark.weaver
def test_claim2_in_every_output(tmp_path):
    text = "".join(_line("order_created", status="ok", **{"code.file.path": "app/orders.py",
                                                           "code.line.number": 42}) + "\n" for _ in range(3))
    text += _line("user_logged_in", password="hunter2") + "\n"
    (tmp_path / "logs.jsonl").write_text(text, encoding="utf-8")
    r = run_cli("check", "logs.jsonl", "--markdown", "r.md", "--html", "r.html", cwd=tmp_path)
    assert "fix first: the top 2 places cause 100% of the lines with violations" in r.stdout
    assert r.stdout.index("fix first") < r.stdout.index("adr_")            # at the top
    assert "75.0%\t3 lines\tapp/orders.py:42" in r.stdout
    md, page = (tmp_path / "r.md").read_text(encoding="utf-8"), (tmp_path / "r.html").read_text(encoding="utf-8")
    assert md.index("#### Fix first") < md.index("#### Violations by rule")
    assert "`app/orders.py:42`" in md and "<h2>Fix first</h2>" in page
    res = json.loads(run_cli("check", "logs.jsonl", "--json", cwd=tmp_path).stdout)
    assert res["lines_with_violations"] == 4
    assert res["fix_first"][0]["where"] == "app/orders.py:42" and res["fix_first"][0]["lines"] == 3
    for out in (r.stdout, md, page, json.dumps(res)):
        assert "hunter2" not in out


def test_claim2_json_keys_only_added():
    """Every key of the v0.8.1 result is still there; the new ones are added."""
    from conftest import GOLDEN
    res = json.loads((GOLDEN / "bad.json").read_text(encoding="utf-8"))
    old = {"tool", "version", "ok", "records", "skipped_lines", "violations", "by_rule", "findings",
           "findings_truncated", "advice", "baseline", "baselined", "baseline_fixed", "disabled",
           "services", "input"}
    assert old <= set(res)
    # v0.9.0 added four keys, v0.10.0 one
    assert set(res) - old == {"profile", "outside_profile", "lines_with_violations", "fix_first",
                              "outside_profile_unknown_fields"}


# ---------------------------------------------------------------- claim 6: stability

def test_claim6_stability_lists_every_rule_and_env_var():
    import re

    from conftest import ROOT
    from logwarden import data
    doc = (ROOT / "STABILITY.md").read_text(encoding="utf-8")
    block = doc.split("<!-- rule-ids:begin -->")[1].split("<!-- rule-ids:end -->")[0]
    assert set(re.findall(r"`([a-z_]+)`", block)) == set(data.rules())
    src = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "src/logwarden").glob("*.py"))
    env = set(re.findall(r'environ(?:\.get\(|\[)"((?:LOGWARDEN|WEAVER)[A-Z_]*)"', src)) - {"WEAVER_VERSION"}
    assert env and all(f"`{e}`" in doc for e in env), sorted(e for e in env if f"`{e}`" not in doc)
    for p in check.PROFILES["essential"]:
        assert f"`{p}`" in doc.split("**Profiles**")[1].split("\n")[0]
