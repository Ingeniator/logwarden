"""v0.8.0 claims 2-4: Kibana / Elasticsearch exports, Fluent Bit records ([source]), per-service metrics."""
import csv
import io
import json
import re

from conftest import EXAMPLES, run_cli

BAD = (EXAMPLES / "bad.jsonl").read_text(encoding="utf-8")
LINES = BAD.splitlines()
OBJS = [json.loads(ln) for ln in LINES if ln.startswith("{")]
K8S = {"pod_name": "api-7d9f", "namespace_name": "shop", "labels": {"app": "api"}}


def _check(tmp_path, name, content, *args):
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    r = run_cli("check", f, "--json", *args, cwd=tmp_path)
    assert r.returncode in (0, 1), r.stderr
    return json.loads(r.stdout)


def _counts(result):
    return {r["rule"]: r["count"] for r in result["by_rule"]}


def _objects_only(tmp_path):
    """bad.jsonl without its plain-text line: what an Elasticsearch index can hold."""
    return _check(tmp_path, "objs.jsonl", "".join(json.dumps(o) + "\n" for o in OBJS))


def _hits(objs, start=0):
    return [{"_index": "logs-app", "_id": f"id{start + i}", "_score": 1.0, "_source": o}
            for i, o in enumerate(objs)]


def _response(hits):
    return {"took": 3, "timed_out": False, "hits": {"total": {"value": len(hits), "relation": "eq"},
                                                     "max_score": 1.0, "hits": hits}}


def _csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


# ------------------------------------------------------------ claim 2: exports

def test_claim2_every_export_gives_the_same_counts(tmp_path):
    want = _counts(_objects_only(tmp_path))
    assert want and sum(want.values()) >= 8

    source_csv = _csv(["@timestamp", "_id", "_source"],
                      [[o["timestamp"], f"id{i}", json.dumps(o)] for i, o in enumerate(OBJS)])
    keys = sorted({k for o in OBJS for k in o})
    column_csv = _csv(keys, [["" if k not in o else str(o[k]) for k in keys] for o in OBJS])
    response = json.dumps(_response(_hits(OBJS)), indent=2)
    pages_array = json.dumps([_response(_hits(OBJS[:2])), _response(_hits(OBJS[2:], 2))])
    pages_ndjson = "\n".join(json.dumps(_response(_hits(p, s))) for p, s in ((OBJS[:1], 0), (OBJS[1:], 1)))
    elasticdump = "".join(json.dumps(h) + "\n" for h in _hits(OBJS))

    for name, content, fmt in [("source.csv", source_csv, "csv"), ("columns.csv", column_csv, "csv"),
                               ("search.json", response, "es"), ("pages.json", pages_array, "es"),
                               ("pages.ndjson", pages_ndjson, "es"), ("dump.ndjson", elasticdump, "jsonl")]:
        r = _check(tmp_path, name, content)
        assert r["input"]["format"] == fmt, name
        assert _counts(r) == want, name
        assert r["records"] == len(OBJS), name


def test_claim2_ids_and_row_numbers(tmp_path):
    r = _check(tmp_path, "search.json", json.dumps(_response(_hits(OBJS))))
    by_rule = {f["rule"]: f for f in r["findings"]}
    assert by_rule["adr_sensitive_data"]["line"] == 3          # the third hit
    assert by_rule["adr_sensitive_data"]["id"] == "id2"
    text = run_cli("check", tmp_path / "search.json", cwd=tmp_path).stdout
    assert "line 3 (id2)" in text


def test_claim2_csv_types_come_back(tmp_path):
    base = {"timestamp": "2026-11-14T10:00:00Z", "level": "info", "event": "request_completed",
            "logger": "app", "service.name": "api", "service.version": "1.0.0",
            "deployment.environment.name": "production", "http.route": "/orders"}
    header = [*base, "http.response.status_code", "server.port", "kubernetes.pod_name"]
    rows = [[*base.values(), "200", "'-1", "api-1"],          # Kibana escapes a leading '-' with '
            [*base.values(), "1,234", "8080", "api-2"]]       # thousands separator from a field formatter
    r = _check(tmp_path, "typed.csv", _csv(header, rows), "--ignore-field", "kubernetes")
    rules = _counts(r)
    assert "type_mismatch" not in rules and "missing_attribute" not in rules, r["findings"]
    assert r["violations"] == 0


def test_claim2_missing_columns_are_not_reported(tmp_path):
    keys = ["timestamp", "level", "event", "logger", "service.name", "deployment.environment.name"]
    rows = [["2026-11-14T10:00:00Z", "info", "order_created", "app", "api", "production"]]
    r = _check(tmp_path, "partial.csv", _csv(keys, rows))
    assert r["input"]["missing_columns"] == ["service.version"]
    assert not any(f["rule"] == "adr_missing_required" for f in r["findings"])
    text = run_cli("check", tmp_path / "partial.csv", cwd=tmp_path).stdout
    assert "no column for service.version" in text and "Export all fields" in text
    # With _source the whole line is there, so the missing field is reported.
    obj = dict(zip(keys, rows[0]))
    r = _check(tmp_path, "whole.csv", _csv(["_source"], [[json.dumps(obj)]]))
    assert r["input"]["missing_columns"] == []
    assert any(f["rule"] == "adr_missing_required" and f["field"] == "service.version" for f in r["findings"])


def test_claim2_format_override_and_bad_exports(tmp_path):
    (tmp_path / "bad.jsonl").write_text(BAD, encoding="utf-8")
    r = run_cli("check", tmp_path / "bad.jsonl", "--format", "es", cwd=tmp_path)
    assert r.returncode == 2 and "not an Elasticsearch _search response" in r.stderr
    (tmp_path / "broken.json").write_text('{"hits": {"hits": [', encoding="utf-8")
    r = run_cli("check", tmp_path / "broken.json", "--format", "es", cwd=tmp_path)
    assert r.returncode == 2
    r = run_cli("check", tmp_path / "bad.jsonl", "--format", "xml", cwd=tmp_path)
    assert r.returncode == 2
    # --format jsonl on a search response reads it as one JSON line (one record).
    (tmp_path / "s.json").write_text(json.dumps(_response(_hits(OBJS))), encoding="utf-8")
    r = json.loads(run_cli("check", tmp_path / "s.json", "--format", "jsonl", "--json", cwd=tmp_path).stdout)
    assert r["input"]["format"] == "jsonl" and r["records"] == 1


def test_claim2_baseline_catalog_compare_accept_exports(tmp_path):
    export = tmp_path / "search.json"
    export.write_text(json.dumps(_response(_hits(OBJS))), encoding="utf-8")
    assert run_cli("baseline", export, "--output", "base.json", cwd=tmp_path).returncode == 0
    r = run_cli("check", export, "--baseline", "base.json", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    cat = json.loads(run_cli("catalog", export, "--json", cwd=tmp_path).stdout)
    assert sorted(cat["events"]) == sorted(o["event"] for o in OBJS)
    (tmp_path / "objs.jsonl").write_text("".join(json.dumps(o) + "\n" for o in OBJS), encoding="utf-8")
    run_cli("check", "objs.jsonl", "--snapshot", "snap.json", cwd=tmp_path)
    r = json.loads(run_cli("check", export, "--compare", "snap.json", "--json", cwd=tmp_path).stdout)
    assert r["compare"]["new"] == 0 and r["compare"]["fixed"] == 0


# ------------------------------------------------------------ claim 3: Fluent Bit

def _fluent_string(lines):
    return "".join(json.dumps({"date": 1763114400.0, "log": ln, "stream": "stdout", "kubernetes": K8S}) + "\n"
                   for ln in lines)


def _fluent_merged(lines):
    out = []
    for ln in lines:
        if ln.startswith("{"):
            out.append({**json.loads(ln), "@timestamp": "2026-11-14T10:00:00Z", "stream": "stdout",
                        "kubernetes": K8S})
        else:
            out.append({"log": ln, "@timestamp": "2026-11-14T10:00:00Z", "stream": "stdout", "kubernetes": K8S})
    return "".join(json.dumps(o) + "\n" for o in out)


def _fluent_key(lines):
    out = []
    for ln in lines:
        rec = {"log": ln, "stream": "stdout", "kubernetes": K8S}
        if ln.startswith("{"):
            rec["log_processed"] = json.loads(ln)
        out.append(rec)
    return "".join(json.dumps(o) + "\n" for o in out)


def _shape(result):
    return sorted((f["line"], f["rule"], f["field"] or "") for f in result["findings"])


def test_claim3_fluent_bit_records_give_the_bare_findings(tmp_path):
    bare = _check(tmp_path, "bare.jsonl", BAD)
    ignore = ["--ignore-field", "kubernetes", "--ignore-field", "stream", "--ignore-field", "date",
              "--ignore-field", "@timestamp"]
    s = _check(tmp_path, "string.jsonl", _fluent_string(LINES), "--message-field", "log", *ignore)
    assert _shape(s) == _shape(bare)
    assert any(f["rule"] == "adr_non_json_line" for f in s["findings"])   # the plain-text `log`
    # Merge_Log On: fields at the top level; the plain-text line keeps its `log` string.
    m = _check(tmp_path, "merged.jsonl", _fluent_merged(LINES), "--message-field", "log", *ignore)
    assert _shape(m) == _shape(bare)
    # Merge_Log_Key log_processed: the object under a key; lines that weren't JSON have none.
    k = _check(tmp_path, "key.jsonl", _fluent_key(LINES), "--message-field", "log_processed", *ignore)
    shape_k = _shape(k)
    assert [x for x in shape_k if x[1] != "missing_attribute"] != []
    assert [x for x in _shape(bare) if x[0] != 2] == [x for x in shape_k if x[0] != 2]


def test_claim3_without_ignore_fields_pipeline_fields_are_unknown(tmp_path):
    r = _check(tmp_path, "string.jsonl", _fluent_string(LINES[:1]))
    assert any(f["rule"] == "missing_attribute" and f["field"] in ("kubernetes", "stream", "date")
               for f in r["findings"])


def test_claim3_config_source_section_matches_the_options(tmp_path):
    (tmp_path / "logwarden.toml").write_text(
        '[source]\nmessage_field = "log"\nignore_fields = ["kubernetes", "stream", "date"]\n', encoding="utf-8")
    via_config = _check(tmp_path, "string.jsonl", _fluent_string(LINES))
    bare = _check(tmp_path, "bare.jsonl", BAD)
    assert _shape(via_config) == _shape(bare)
    (tmp_path / "logwarden.toml").write_text('[source]\nignore_fields = "kubernetes"\n', encoding="utf-8")
    r = run_cli("check", tmp_path / "bare.jsonl", cwd=tmp_path)
    assert r.returncode == 2 and "ignore_fields" in r.stderr


# ------------------------------------------------------------ claim 4: per service

def _three_services():
    def line(svc, **extra):
        o = {"timestamp": "2026-11-14T10:00:00Z", "level": "info", "event": "order_created", "logger": "app",
             "service.version": "1.0.0", "deployment.environment.name": "production", **extra}
        if svc:
            o["service.name"] = svc
        return json.dumps(o)
    return "\n".join([
        line("orders", status=200), line("orders", status=201, password="hunter2"), line("orders"),
        line("billing", http_route="/x"),
        line("search"), line("search"),
        line(None),
        "plain text line",
    ]) + "\n"


def test_claim4_breakdown_in_every_output(tmp_path):
    (tmp_path / "logs.jsonl").write_text(_three_services(), encoding="utf-8")
    r = run_cli("check", "logs.jsonl", "--json", "--markdown", "r.md", "--html", "r.html",
                "--metrics", "m.prom", cwd=tmp_path)
    res = json.loads(r.stdout)
    svc = {s["service"]: s for s in res["services"]}
    assert set(svc) == {"orders", "billing", "search", "(none)"}
    o = svc["orders"]
    assert o["lines"] == 3 and o["lines_with_violations"] == 2 and o["compliance"] == 33.3
    assert o["by_rule"]["adr_banned_synonym"] == 2 and o["top_rule"] == "adr_banned_synonym"
    assert svc["search"] == {"service": "search", "lines": 2, "violations": 0, "lines_with_violations": 0,
                             "compliance": 100.0, "by_rule": {}, "top_rule": None}
    assert svc["(none)"]["lines"] == 2      # a line without service.name and the plain-text line
    assert res["services"][0]["service"] == "orders"       # most violations first
    assert all(f["service"] for f in res["findings"])
    text = run_cli("check", "logs.jsonl", cwd=tmp_path).stdout
    assert "by service:" in text and "orders\t3 lines" in text
    md, page = (tmp_path / "r.md").read_text(encoding="utf-8"), (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "#### By service" in md and "| `orders` | 3 |" in md
    assert "<h2>By service</h2>" in page and "33.3%" in page
    prom = (tmp_path / "m.prom").read_text(encoding="utf-8")
    for out in (r.stdout, text, md, page, prom):
        assert "hunter2" not in out


_SAMPLE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{([a-zA-Z_][a-zA-Z0-9_]*="(\\.|[^"\\])*",?)*\})? '
                     r'(-?\d+(\.\d+)?)$')


def test_claim4_metrics_file_is_prometheus_text(tmp_path):
    (tmp_path / "logs.jsonl").write_text(_three_services(), encoding="utf-8")
    run_cli("check", "logs.jsonl", "--metrics", "m.prom", cwd=tmp_path)
    prom = (tmp_path / "m.prom").read_text(encoding="utf-8")
    families = set()
    for ln in prom.splitlines():
        if ln.startswith("# TYPE"):
            families.add(ln.split()[2])
        elif not ln.startswith("# HELP"):
            assert _SAMPLE.match(ln), ln
    assert families == {"logwarden_lines_total", "logwarden_lines_with_violations_total",
                        "logwarden_violations_total"}
    assert 'logwarden_lines_total{service="orders"} 3' in prom
    assert 'logwarden_lines_with_violations_total{service="orders"} 2' in prom
    assert 'logwarden_violations_total{service="orders",rule="adr_banned_synonym"} 2' in prom
    assert 'logwarden_lines_total{service="search"} 2' in prom


def test_claim4_service_filter(tmp_path):
    (tmp_path / "logs.jsonl").write_text(_three_services(), encoding="utf-8")
    r = json.loads(run_cli("check", "logs.jsonl", "--json", "--service", "billing", "--service", "search",
                           cwd=tmp_path).stdout)
    assert {s["service"] for s in r["services"]} == {"billing", "search"}
    assert r["records"] == 3 and r["skipped_lines"] == 0
    assert all(f["service"] == "billing" for f in r["findings"])


# ------------------------------------------------------------ real exports (tests/data/exports/README.md)

def test_claim2_3_real_exports_give_the_bare_counts(tmp_path):
    from conftest import DATA
    want = _counts(_check(tmp_path, "bare.jsonl", BAD))
    fluent = ["--message-field", "log", "--ignore-field", "kubernetes", "--ignore-field", "stream",
              "--ignore-field", "@timestamp"]
    exports = DATA / "exports"
    for name, fmt in [("es-raw.search.json", "es"), ("es-merged.scroll.ndjson", "es"),
                      ("es-raw.elasticdump.ndjson", "jsonl"), ("kibana-raw-all-fields.csv", "csv"),
                      ("kibana-merged-all-fields.csv", "csv"), ("fluent-bit-raw.jsonl", "jsonl"),
                      ("fluent-bit-merged.jsonl", "jsonl")]:
        r = run_cli("check", exports / name, "--json", *fluent, cwd=tmp_path)
        res = json.loads(r.stdout)
        assert res["input"]["format"] == fmt, name
        assert _counts(res) == want, name
        assert res["input"]["missing_columns"] == [], name
    # Without [source], Fluent Bit's own fields are unknown fields.
    r = json.loads(run_cli("check", exports / "fluent-bit-raw.jsonl", "--json", cwd=tmp_path).stdout)
    assert _counts(r) != want
    # Picked columns without `log`: the plain-text line can't be rebuilt; the others still are.
    r = json.loads(run_cli("check", exports / "kibana-merged-picked-columns.csv", "--json", *fluent,
                           cwd=tmp_path).stdout)
    assert "adr_non_json_line" not in _counts(r)
    assert all(_counts(r).get(k, 0) >= n for k, n in want.items() if k != "adr_non_json_line")
