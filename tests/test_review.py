"""v0.7.0: snapshots and --compare (claim 1), HTML report (2), Markdown summary (3),
event catalog (5) and the adr_event_catalog rule (6)."""
import json
import re
import sys
from html.parser import HTMLParser

import pytest

from conftest import EXAMPLES, GOLDEN
from logwarden import report
from test_new_rules import _line

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

A = _line("a_done", status="ok")                    # adr_banned_synonym
B = _line("Bad Name")                               # adr_event_name
C = _line("c_done", password="hunter2")             # adr_sensitive_data
OK = _line("ok_done")


def _rules(items):
    return sorted(i["rule"] for i in items)


def _snap(cli, cwd, name, *lines):
    (cwd / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    cli("check", f"{name}.jsonl", "--snapshot", f"{name}.json", cwd=cwd)
    return json.loads((cwd / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- claim 1

@pytest.mark.weaver
def test_claim1_new_fixed_unchanged(cli, tmp_path):
    base = _snap(cli, tmp_path, "base", A, OK, B)
    assert base["kind"] == "snapshot" and base["events"] == {"a_done": 1, "ok_done": 1, "Bad Name": 1}
    assert _rules(base["entries"]) == ["adr_banned_synonym", "adr_event_name"]
    # B moves from line 3 to line 1: still the same finding, not new.
    (tmp_path / "run.jsonl").write_text("\n".join([B, OK, C, C]) + "\n", encoding="utf-8")
    r = cli("check", "run.jsonl", "--compare", "base.json", "--json", cwd=tmp_path)
    c = json.loads(r.stdout)["compare"]
    assert (c["new"], c["fixed"], c["unchanged"]) == (1, 1, 1)
    assert _rules(c["new_findings"]) == ["adr_sensitive_data"] and c["new_findings"][0]["line"] == 3
    assert _rules(c["fixed_entries"]) == ["adr_banned_synonym"]
    assert c["new_events"] == ["c_done"] and c["gone_events"] == ["a_done"]
    assert c["base"] == "base.json"
    text = cli("check", "run.jsonl", "--compare", "base.json", cwd=tmp_path).stdout
    assert "compared with base.json: 1 new, 1 fixed, 1 unchanged; new events: c_done" in text

    # --compare only reports: same exit code and same result otherwise.
    plain = cli("check", "run.jsonl", "--json", cwd=tmp_path)
    assert plain.returncode == r.returncode == 1
    res = json.loads(r.stdout)
    res.pop("compare")
    assert res == json.loads(plain.stdout)


@pytest.mark.weaver
def test_claim1_disable_and_baseline_file(cli, tmp_path):
    _snap(cli, tmp_path, "base", A)
    (tmp_path / "run.jsonl").write_text(B + "\n", encoding="utf-8")
    (tmp_path / "logwarden.toml").write_text('disable = { adr_event_name = "test" }\n', encoding="utf-8")
    c = json.loads(cli("check", "run.jsonl", "--compare", "base.json", "--json", cwd=tmp_path).stdout)["compare"]
    assert (c["new"], c["fixed"]) == (0, 1)
    (tmp_path / "logwarden.toml").unlink()
    # A baseline file works as --compare; it has no events, so no event diff.
    assert cli("baseline", "base.jsonl", "--output", "bl.json", cwd=tmp_path).returncode == 0
    c = json.loads(cli("check", "run.jsonl", "--compare", "bl.json", "--json", cwd=tmp_path).stdout)["compare"]
    assert (c["new"], c["fixed"], c["unchanged"]) == (1, 1, 0)
    assert c["new_events"] is None and c["gone_events"] is None


@pytest.mark.parametrize("content", [None, "{}", "not json", '{"format": 1, "entries": [{"rule": "x"}]}',
                                     '{"format": 1, "entries": [], "events": [1]}'])
def test_claim1_bad_compare_file(cli, tmp_path, content):
    if content is not None:
        (tmp_path / "base.json").write_text(content, encoding="utf-8")
    r = cli("check", "-", "--compare", "base.json", stdin=OK + "\n", cwd=tmp_path)
    assert r.returncode == 2 and "--compare" in r.stderr, r.stderr


# ---------------------------------------------------------------- claim 2

class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.urls += [v for k, v in attrs if k in ("src", "href")]


def _parse(page):
    p = _Tags()
    p.feed(page)
    p.close()
    return p


@pytest.mark.weaver
def test_claim2_html_report(cli, tmp_path):
    golden = json.loads((GOLDEN / "bad.json").read_text(encoding="utf-8"))
    r = cli("check", EXAMPLES / "bad.jsonl", "--html", "r.html", cwd=tmp_path)
    assert r.returncode == 1
    page = (tmp_path / "r.html").read_text(encoding="utf-8")
    p = _parse(page)
    assert "script" not in p.tags and "link" not in p.tags and "img" not in p.tags
    assert p.urls and all(u.startswith("https://github.com/Ingeniator/logwarden/blob/") for u in p.urls)
    for rule in golden["by_rule"]:
        assert f"<code>{rule['rule']}</code>" in page
    assert page.count("<tr>") >= golden["violations"]       # every finding has a row
    assert "Compared with the base" not in page
    assert "Events in this run (" in page


@pytest.mark.weaver
def test_claim2_escaping_and_compare_section(cli, tmp_path):
    _snap(cli, tmp_path, "base", OK)
    evil = _line("<script>alert(1)</script>", **{"x<b>": "<img src=x onerror=alert(1)>"})
    (tmp_path / "run.jsonl").write_text(evil + "\n", encoding="utf-8")
    cli("check", "run.jsonl", "--compare", "base.json", "--html", "r.html", "--markdown", "r.md", cwd=tmp_path)
    page = (tmp_path / "r.html").read_text(encoding="utf-8")
    p = _parse(page)
    assert "script" not in p.tags and "img" not in p.tags
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<code>x&lt;b&gt;</code>" in page
    assert "Compared with the base" in page
    md = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "<script>" not in md and "<img" not in md and "&lt;script&gt;" in md


@pytest.mark.weaver
def test_claim2_o_dir_writes_reports(cli, tmp_path):
    cli("check", EXAMPLES / "good.jsonl", "-o", "out", cwd=tmp_path)
    for name in ("report.html", "report.md", "snapshot.json", "result.json"):
        assert (tmp_path / "out" / name).is_file(), name
    assert json.loads((tmp_path / "out/snapshot.json").read_text(encoding="utf-8"))["kind"] == "snapshot"


# ---------------------------------------------------------------- claim 3

def _normalise(md):
    return re.sub(r"logwarden/blob/[^/]+/|logwarden \d[^<]*", "VERSION", md)


@pytest.mark.weaver
def test_claim3_markdown_golden(cli, tmp_path):
    cli("check", EXAMPLES / "good.jsonl", "--snapshot", "base.json", cwd=tmp_path)
    cli("check", EXAMPLES / "bad.jsonl", "--compare", "base.json", "--markdown", "r.md", cwd=tmp_path)
    got = _normalise((tmp_path / "r.md").read_text(encoding="utf-8"))
    assert got == _normalise((GOLDEN / "bad-vs-good.md").read_text(encoding="utf-8"))


def test_claim3_markdown_fits_a_comment():
    f = {"line": 1, "event": "e" * 50, "rule": "adr_event_name", "field": None, "message": "m" * 400}
    result = {"version": "0", "records": 10_000, "skipped_lines": 0, "violations": 10_000, "baselined": 0,
              "by_rule": [{"rule": f"r{i}", "count": 1, "title": "t" * 80, "hint": "h" * 300,
                           "doc": "docs/rules.md#x", "example": "x"} for i in range(200)],
              "findings": [f] * 500, "findings_truncated": True, "advice": [], "disabled": [],
              "baseline_fixed": 0,
              "compare": {"base": "b.json", "new": 10_000, "fixed": 3000, "unchanged": 0,
                          "new_findings": [f] * 500, "new_events": ["n" * 60] * 500, "gone_events": [],
                          "fixed_entries": [{"rule": "r", "field": "f" * 100, "logger": "", "event": "e" * 100,
                                             "count": 1}] * 500}}
    md = report.markdown(result)
    assert len(md) <= report.COMMENT_LIMIT < 65_536
    assert md.startswith(report.MARKER) and "more new findings" in md
    tiny = report.markdown(result, limit=2000)
    assert len(tiny) <= 2000 and "report cut to fit" in tiny


# ---------------------------------------------------------------- claim 5

GOOD_EVENTS = {
    "billing_sync_complete": {"count": 1, "levels": ["info"], "loggers": ["app.services.billing"],
                              "fields": ["duration_ms", "http.response.status_code", "http.route", "request_id"],
                              "first_line": 1},
    "upstream_call_failed": {"count": 1, "levels": ["error"], "loggers": ["app.clients.llm"],
                             "fields": ["duration_ms", "error.type", "http.response.status_code", "server.address",
                                        "upstream_error_message", "upstream_request_id"],
                             "first_line": 2},
}


def test_claim5_catalog_lists_events(cli, tmp_path):
    r = cli("catalog", EXAMPLES / "good.jsonl", "--json", cwd=tmp_path)
    assert r.returncode == 0 and json.loads(r.stdout)["events"] == GOOD_EVENTS
    text = cli("catalog", EXAMPLES / "good.jsonl", cwd=tmp_path).stdout
    assert text.startswith("2 events") and "upstream_call_failed\terror\tline 2" in text
    md = cli("catalog", EXAMPLES / "good.jsonl", "--markdown", cwd=tmp_path).stdout
    assert md.startswith("| Event | Count |") and "| `billing_sync_complete` | 1 | info |" in md


def test_claim5_catalog_file_merges(cli, tmp_path):
    r = cli("catalog", EXAMPLES / "good.jsonl", "-o", "ev.toml", cwd=tmp_path)
    assert r.returncode == 0 and "2 events, 2 new" in r.stdout
    cat = tomllib.loads((tmp_path / "ev.toml").read_text(encoding="utf-8"))["events"]
    assert cat == {"billing_sync_complete": {"description": "", "levels": ["info"]},
                   "upstream_call_failed": {"description": "", "levels": ["error"]}}
    # Hand edits: a description, an extra key, an event no run produced.
    (tmp_path / "ev.toml").write_text(
        '[events.billing_sync_complete]\ndescription = "Billing synced."\nlevels = ["info"]\nowner = "billing"\n'
        '[events.rare_path_taken]\ndescription = "Only on Sundays."\n')
    (tmp_path / "new.jsonl").write_text("\n".join([
        _line("billing_sync_complete", level="warning"), _line('odd "name".x')]) + "\n")
    r = cli("catalog", "new.jsonl", "-o", "ev.toml", cwd=tmp_path)
    assert "3 events, 1 new" in r.stdout
    cat = tomllib.loads((tmp_path / "ev.toml").read_text(encoding="utf-8"))["events"]
    assert cat["billing_sync_complete"] == {"description": "Billing synced.", "levels": ["info", "warning"],
                                            "owner": "billing"}
    assert cat["rare_path_taken"]["description"] == "Only on Sundays."
    assert cat['odd "name".x'] == {"description": "", "levels": ["info"]}


def test_claim5_broken_catalog_file(cli, tmp_path):
    (tmp_path / "ev.toml").write_text("[events\n", encoding="utf-8")
    r = cli("catalog", EXAMPLES / "good.jsonl", "-o", "ev.toml", cwd=tmp_path)
    assert r.returncode == 2 and "not valid TOML" in r.stderr


# ---------------------------------------------------------------- claim 6

CAT = '[events.ok_done]\ndescription = "ok"\n[events.a_done]\ndescription = "a"\n'


@pytest.mark.weaver
def test_claim6_event_not_in_catalog(cli, tmp_path):
    (tmp_path / "ev.toml").write_text(CAT, encoding="utf-8")
    text = "\n".join([OK, _line("new_thing_done"), _line("Not Snake")]) + "\n"
    assert all(f["rule"] != "adr_event_catalog"     # no config: the rule doesn't run
               for f in json.loads(cli("check", "-", "--json", stdin=text, cwd=tmp_path).stdout)["findings"])
    (tmp_path / "logwarden.toml").write_text('events = "ev.toml"\n', encoding="utf-8")
    r = cli("check", "-", "--json", stdin=text, cwd=tmp_path)
    found = [f for f in json.loads(r.stdout)["findings"] if f["rule"] == "adr_event_catalog"]
    assert r.returncode == 1 and len(found) == 1           # "Not Snake" is adr_event_name's
    assert found[0]["line"] == 2 and "'new_thing_done' is not in ev.toml" in found[0]["message"]
    assert cli("check", "-", stdin=OK + "\n", cwd=tmp_path).returncode == 0

    # Baseline and disable apply.
    (tmp_path / "l.jsonl").write_text(_line("new_thing_done") + "\n", encoding="utf-8")
    assert cli("baseline", "l.jsonl", cwd=tmp_path).returncode == 0
    assert cli("check", "l.jsonl", "--baseline", ".logwarden-baseline.json", cwd=tmp_path).returncode == 0
    (tmp_path / "logwarden.toml").write_text('events = "ev.toml"\ndisable = { adr_event_catalog = "later" }\n', encoding="utf-8")
    assert cli("check", "l.jsonl", cwd=tmp_path).returncode == 0


@pytest.mark.parametrize("content,msg", [(None, "not found"), ("[events\n", "not valid TOML"),
                                         ("events = 1\n", "expected [events.<name>] tables")])
def test_claim6_bad_catalog_config(cli, tmp_path, content, msg):
    (tmp_path / "logwarden.toml").write_text('events = "ev.toml"\n', encoding="utf-8")
    if content is not None:
        (tmp_path / "ev.toml").write_text(content, encoding="utf-8")
    r = cli("check", "-", stdin=OK + "\n", cwd=tmp_path)
    assert r.returncode == 2 and msg in r.stderr, r.stderr
