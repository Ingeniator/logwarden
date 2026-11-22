"""v0.9.0 claim 3 (fetch from Elasticsearch, sample note) and claim 4 (Pushgateway push)."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import EXAMPLES, run_cli
from logwarden import fetch


class Stub:
    """A stand-in Elasticsearch / Pushgateway that records requests."""

    def __init__(self, status=200, hits=None, total=None, relation="eq"):
        self.status, self.requests = status, []
        lines = (EXAMPLES / "bad.jsonl").read_text(encoding="utf-8").splitlines()
        hits = hits if hits is not None else [
            {"_id": f"id{i}", "_source": {"@timestamp": "2026-11-22T10:00:00Z", "log": ln,
                                          "kubernetes": {"pod_name": "api-1"}}}
            for i, ln in enumerate(x for x in lines if x.strip())]
        self.body = {"took": 1, "hits": {"total": {"value": len(hits) if total is None else total,
                                                   "relation": relation}, "hits": hits}}
        stub = self

        class H(BaseHTTPRequestHandler):
            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                stub.requests.append({"method": self.command, "path": self.path,
                                      "headers": dict(self.headers), "body": self.rfile.read(n)})
                data = json.dumps(stub.body).encode() if stub.status == 200 else b'{"error":"x"}'
                self.send_response(stub.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_POST = do_PUT = _handle

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def stub():
    made = []

    def make(**kw):
        made.append(Stub(**kw))
        return made[-1]
    yield make
    for s in made:
        s.close()


def test_claim3_query_and_auth(stub):
    es = stub()
    raw, got, matched = fetch.es(es.url, "logs-shop-*", since="30m", size=500, time_field="ts",
                                 query_string="kubernetes.namespace_name:shop", auth="elastic:s3cret")
    req = es.requests[0]
    assert req["method"] == "POST" and req["path"] == "/logs-shop-*/_search"
    body = json.loads(req["body"])
    assert body["size"] == 500 and body["track_total_hits"] is True
    assert body["query"]["bool"]["filter"] == [{"range": {"ts": {"gte": "now-30m"}}},
                                               {"query_string": {"query": "kubernetes.namespace_name:shop"}}]
    assert body["sort"] == [{"ts": {"order": "desc", "unmapped_type": "date"}}]
    assert req["headers"]["Authorization"] == "Basic " + base64.b64encode(b"elastic:s3cret").decode()
    assert got == matched == 4 and json.loads(raw)["hits"]["hits"]


@pytest.mark.parametrize("value,header", [
    ("ApiKey abc==", "ApiKey abc=="), ("Basic eHk6eg==", "Basic eHk6eg=="), ("Bearer t", "Bearer t"),
    ("abc==", "ApiKey abc=="), ("", None)])
def test_claim3_auth_forms(value, header):
    assert fetch.auth_header(value).get("Authorization") == header


@pytest.mark.parametrize("status,msg", [(401, "credentials were refused"), (403, "can't read this index"),
                                        (404, "no index matches 'logs-*'"), (500, "HTTP 500")])
def test_claim3_http_errors(stub, tmp_path, status, msg):
    es = stub(status=status)
    r = run_cli("fetch", "--es", es.url, "--index", "logs-*", cwd=tmp_path)
    assert r.returncode == 2 and msg in r.stderr
    assert not (tmp_path / "logwarden-es.json").exists()


def test_claim3_bad_input_and_unreachable(stub, tmp_path):
    es = stub(hits=[])
    r = run_cli("fetch", "--es", es.url, "--index", "logs-*", "--since", "2h", cwd=tmp_path)
    assert r.returncode == 2 and "no log lines in the last 2h of 'logs-*'" in r.stderr
    for args, msg in [(["--since", "1 hour"], "--since must be"), (["--size", "20000"], "--size must be"),
                      (["--size", "0"], "--size must be")]:
        r = run_cli("fetch", "--es", es.url, "--index", "x", *args, cwd=tmp_path)
        assert r.returncode == 2 and msg in r.stderr, args
    r = run_cli("fetch", "--es", "es:9200", "--index", "x", cwd=tmp_path)
    assert r.returncode == 2 and "must start with http" in r.stderr
    r = run_cli("fetch", "--es", "http://127.0.0.1:1", "--index", "x", cwd=tmp_path)
    assert r.returncode == 2 and "can't reach Elasticsearch" in r.stderr


def test_claim3_credentials_never_on_the_command_line(stub, tmp_path):
    es = stub()
    r = run_cli("fetch", "--es", es.url, "--index", "logs-*", cwd=tmp_path,
                env={"LOGWARDEN_ES_AUTH": "ApiKey top-secret"})
    assert r.returncode == 0, r.stderr
    assert es.requests[0]["headers"]["Authorization"] == "ApiKey top-secret"
    assert "top-secret" not in r.stderr + r.stdout
    assert "--auth" not in run_cli("fetch", "--help").stdout


@pytest.mark.weaver
def test_claim3_fetch_then_check_with_sample_note(stub, tmp_path):
    es = stub(total=40_000)
    r = run_cli("fetch", "--es", es.url, "--index", "logs-*", "-o", "export.json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "fetched the newest 4 of 40,000 log lines from the last 1h of 'logs-*'" in r.stderr
    r = run_cli("check", "export.json", "--message-field", "log", "--ignore-field", "kubernetes",
                "--markdown", "r.md", "--json", cwd=tmp_path)
    res = json.loads(r.stdout)
    assert res["input"] == {"format": "es", "missing_columns": [], "matched": 40_000}
    assert res["violations"] == 10                        # the same as bad.jsonl
    note = "Sample: the export holds 4 of the 40,000 log lines that matched the query"
    assert note in (tmp_path / "r.md").read_text(encoding="utf-8")
    assert note in run_cli("check", "export.json", "--message-field", "log", "--ignore-field", "kubernetes",
                           cwd=tmp_path).stdout


def test_claim3_no_sample_note_when_everything_was_fetched_or_unknown():
    from logwarden import sources
    page = lambda total: json.dumps({"hits": {"total": total, "hits": [{"_source": {"a": 1}}]}})  # noqa: E731
    assert sources.load(page({"value": 1, "relation": "eq"})).matched == 1
    assert sources.load(page({"value": 10000, "relation": "gte"})).matched is None   # a lower bound only
    assert sources.load(page(7)).matched == 7                                        # Elasticsearch 6
    assert sources.load(json.dumps({"hits": {"hits": [{"_source": {"a": 1}}]}})).matched is None
    from logwarden import report
    base = {"records": 4, "skipped_lines": 0}
    assert report.sample_note({**base, "input": {"matched": 4}}) is None
    assert report.sample_note({**base, "input": {"matched": None}}) is None


@pytest.mark.weaver
def test_claim4_pushgateway(stub, tmp_path):
    pg = stub()
    (tmp_path / "bad.jsonl").write_text((EXAMPLES / "bad.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    r = run_cli("check", "bad.jsonl", "--metrics", "m.prom", "--pushgateway", pg.url, cwd=tmp_path)
    assert r.returncode == 1 and "pushed metrics to" in r.stderr
    req = pg.requests[0]
    assert req["method"] == "PUT" and req["path"] == "/metrics/job/logwarden"
    assert req["body"].decode() == (tmp_path / "m.prom").read_text(encoding="utf-8")   # same as --metrics
    run_cli("check", "bad.jsonl", "--pushgateway", pg.url + "/metrics/job/logwarden/project/shop", cwd=tmp_path,
            env={"LOGWARDEN_PUSHGATEWAY_AUTH": "u:p"})
    assert pg.requests[1]["path"] == "/metrics/job/logwarden/project/shop"
    assert pg.requests[1]["headers"]["Authorization"].startswith("Basic ")
    bad = stub(status=400)
    r = run_cli("check", "bad.jsonl", "--pushgateway", bad.url, cwd=tmp_path)
    assert r.returncode == 2 and "Pushgateway" in r.stderr and "HTTP 400" in r.stderr
