"""v0.12.0: check --signal metrics (experimental) on Prometheus / OpenMetrics text."""
import json
from pathlib import Path

import pytest

from logwarden import metrics

DATA = Path(__file__).parent / "data" / "metrics"
REG = metrics.Registry(
    {"http.server.request.duration": {"instrument": "histogram", "unit": "s"},
     "http.server.request.body.size": {"instrument": "histogram", "unit": "By"},
     "db.client.connection.count": {"instrument": "updowncounter", "unit": "{connection}"},
     "process.cpu.utilization": {"instrument": "gauge", "unit": "1"},
     "system.network.packets": {"instrument": "counter", "unit": "{packet}"},
     "process.cpu.time": {"instrument": "counter", "unit": "s"}},
    {"http.request.method": "string", "http.response.status_code": "int", "user.id": "string"})


def _fam(text):
    return {f.name: f for f in metrics.parse(text)}


# ---------------------------------------------------------------- claim 1: input

@pytest.mark.parametrize("prom,typ,otel,unit,instrument", [
    ("http_server_request_duration_seconds", "histogram", "http.server.request.duration", "s", "histogram"),
    ("http_server_request_body_size_bytes", "histogram", "http.server.request.body.size", "By", "histogram"),
    ("process_cpu_time_seconds_total", "counter", "process.cpu.time", "s", "counter"),
    ("process_cpu_utilization_ratio", "gauge", "process.cpu.utilization", "1", "gauge"),
    ("system_network_packets_total", "counter", "system.network.packets", "{packet}", "counter"),
    ("db_client_connection_count", "gauge", "db.client.connection.count", "{connection}", "updowncounter"),
    ("http.server.request.duration", "histogram", "http.server.request.duration", "s", "histogram"),
    ("http_server_request_duration_seconds", "summary", "http.server.request.duration", "s", "histogram"),
])
def test_claim1_translation(prom, typ, otel, unit, instrument):
    dotted = "." in prom
    name = f'"{prom}"' if dotted else prom
    unit_line = f"# UNIT {name} s\n" if dotted else ""
    if typ in ("histogram", "summary"):
        sample = f'{{"{prom}_count"}} 1\n' if dotted else f"{prom}_count 1\n{prom}_sum 1\n"
    else:
        sample = f"{prom} 1\n"
    f = metrics.parse(f"# TYPE {name} {typ}\n{unit_line}{sample}")[0]
    t = metrics.translate(f, REG)
    assert (t.otel, t.unit, t.instrument, t.known) == (otel, unit, instrument, True)


def test_claim1_parser():
    fams = _fam(r'''# HELP x_total A "quoted" help.
# TYPE x_total counter
x_total{a="q\"uote",b="new\nline",c="back\\slash"} 1 1700000000000
x_created{a="q\"uote",b="new\nline",c="back\\slash"} 1.7e9
# TYPE h histogram
h_bucket{le="1"} 1
h_bucket{le="+Inf"} 2
h_sum 3
h_count 2
h_created 1.7e9
# TYPE "my.metric" gauge
{"my.metric", "my.label"="v"} 4
# EOF
''')
    assert list(fams) == ["x_total", "h", "my.metric"]       # _created folded into its metric
    (key,) = fams["x_total"].series
    assert dict(key) == {"a": 'q"uote', "b": "new\nline", "c": "back\\slash"}
    assert fams["h"].series[()]["buckets"] == [(1.0, 1.0), (float("inf"), 2.0)]
    assert list(fams["my.metric"].series) == [(("my.label", "v"),)]
    with pytest.raises(metrics.MetricsError):
        metrics.parse("not a metric line at all {")


def test_claim1_histogram_sample():
    f = metrics.parse('# TYPE h_seconds histogram\nh_seconds_bucket{le="0.1"} 3\n'
                      'h_seconds_bucket{le="+Inf"} 4\nh_seconds_sum 0.5\nh_seconds_count 4\n')[0]
    p = metrics.to_sample(metrics.translate(f, REG), REG)["metric"]["data_points"][0]
    assert (p["count"], p["bucket_counts"], p["explicit_bounds"]) == (4, [3, 1], [0.1])


def test_claim1_label_types_from_registry():
    f = metrics.parse('# TYPE http_server_request_duration_seconds histogram\n'
                      'http_server_request_duration_seconds_count{http_response_status_code="200",'
                      'http_request_method="GET"} 1\n')[0]
    attrs = metrics.to_sample(metrics.translate(f, REG), REG)["metric"]["data_points"][0]["attributes"]
    assert {a["name"]: a["value"] for a in attrs} == {"http.response.status_code": 200, "http.request.method": "GET"}


# ---------------------------------------------------------------- claim 3: cardinality

def _series(n, label="kind"):
    return "# TYPE shop_orders_total counter\n" + "".join(
        f'shop_orders_total{{{_q(label)}="v{i}"}} 1\n' for i in range(n))


def _q(label):
    return f'"{label}"' if "." in label else label


@pytest.mark.parametrize("n,over", [(1000, False), (1001, True)])
def test_claim3_budget(n, over):
    t = metrics.translate(metrics.parse(_series(n))[0], REG)
    found = metrics.cardinality(t, 1000)
    assert bool(found) is over
    if over:
        assert found[0]["series"] == 1001 and "'kind' (1001)" in found[0]["message"]


@pytest.mark.parametrize("label,unbounded", [
    ("user_id", True), ("user.id", True), ("session_id", True), ("order_id", True),
    ("customer_email", True), ("url_path", True), ("url_full", True), ("error_message", True),
    ("timestamp", True), ("service_instance_id", False), ("http_route", False), ("status", False),
    ("kind", False), ("valid", False)])
def test_claim3_unbounded_labels(label, unbounded):
    reg = metrics.Registry(REG.metrics, {**REG.attributes, "service.instance.id": "string"})
    t = metrics.translate(metrics.parse(_series(3, label))[0], reg)
    assert bool(metrics.cardinality(t, 1000)) is unbounded


def test_claim3_300_user_ids_one_finding():
    t = metrics.translate(metrics.parse(_series(300, "user_id"))[0], REG)
    (f,) = metrics.cardinality(t, 1000)
    assert (f["rule"], f["field"], f["series"]) == ("adr_metric_cardinality", "user_id", 300)
    assert "300 distinct values" in f["message"] and "v1" not in f["message"]


# ---------------------------------------------------------------- claim 2: findings (real Weaver)

@pytest.mark.weaver
def test_claim2_spike_findings(cli, tmp_path):
    proc = cli("check", "--signal", "metrics", DATA / "spike.prom", "--json", cwd=tmp_path)
    assert proc.returncode == 1, proc.stderr
    r = json.loads(proc.stdout)
    got = {(f["metric"], f["field"], f["rule"]) for f in r["findings"]}
    assert ("api_latency_ms", None, "missing_metric") in got
    assert ("http_client_request_duration_milliseconds", None, "unit_mismatch") in got
    assert ("http_server_request_duration_total", None, "unexpected_instrument") in got
    assert ("shop_orders_total", "status", "adr_banned_synonym") in got
    assert ("shop_signups_total", "customer", "adr_sensitive_data") in got
    assert not any(m == "http_server_request_duration_seconds" for m, _, _ in got)   # the correct one
    assert r["library_metrics_skipped"] == 1 and r["metrics"] == 6                   # process_*
    hint = next(f["message"] for f in r["findings"] if f["metric"] == "api_latency_ms")
    assert "http.server.request.duration" in hint and "http_server_request_duration_seconds" in hint
    assert {"missing_metric", "unit_mismatch", "unexpected_instrument"} <= set(r["hints"])
    # grouped: shop_orders_total's 2 series give one synonym finding, with the count
    (syn,) = [f for f in r["findings"] if f["rule"] == "adr_banned_synonym"]
    assert syn["series"] == 2
    # no label value in any output
    md = tmp_path / "m.md"
    txt = cli("check", "--signal", "metrics", DATA / "spike.prom", "--markdown", md, cwd=tmp_path).stdout
    for out in (proc.stdout, txt, md.read_text()):
        for value in ("jane.doe", "example.com", "/orders/{id}", "failed"):
            assert value not in out, value


@pytest.mark.weaver
def test_claim2_required_attribute(cli, tmp_path):
    p = tmp_path / "m.prom"
    p.write_text('# TYPE http_server_request_duration_seconds histogram\n'
                 'http_server_request_duration_seconds_count{http_request_method="GET"} 1\n'
                 'http_server_request_duration_seconds_sum{http_request_method="GET"} 1\n')
    r = json.loads(cli("check", "--signal", "metrics", p, "--json", cwd=tmp_path).stdout)
    assert [(f["field"], f["rule"]) for f in r["findings"]] == [("url.scheme", "required_attribute_not_present")]


def test_metrics_usage_errors(cli, tmp_path):
    p = tmp_path / "m.prom"
    p.write_text("x 1\n")
    for extra in (["--baseline", "b.json"], ["--sarif", "s.json"], ["-o", "out"]):
        proc = cli("check", "--signal", "metrics", p, *extra, cwd=tmp_path)
        assert proc.returncode == 2 and "not available for metrics" in proc.stderr, extra
    p.write_text("this is { not prometheus\n")
    proc = cli("check", "--signal", "metrics", p, cwd=tmp_path)
    assert proc.returncode == 2 and "metrics:" in proc.stderr


@pytest.mark.parametrize("base,hint", [
    ("api_latency", "http.server.request.duration"), ("http_requests", "http.server.request.duration"),
    ("http_request_size", "http.server.request.body.size"),
    ("llm_prompt_token_usage", "gen_ai.client.inference.usage.input_tokens"),
    ("llm_completion_token_usage", "gen_ai.client.inference.usage.output_tokens"),
    ("llm_total_token_usage", None), ("orders", None)])
def test_semconv_hint(base, hint):
    names = ["http.server.request.duration", "http.server.request.body.size",
             "gen_ai.client.inference.usage.input_tokens", "gen_ai.client.inference.usage.output_tokens"]
    reg = metrics.Registry({n: {"instrument": "histogram", "unit": "s"} for n in names}, {})
    assert metrics.semconv_hint(base, reg) == hint
