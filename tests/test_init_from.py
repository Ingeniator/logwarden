"""v0.10.0: init --from (claim 1), "ready for standard" (claim 2), pin updates (claim 4)."""
import json
import sys

import pytest

from conftest import ROOT, run_cli
from test_new_rules import _line

LOGS = "".join(x + "\n" for x in [
    _line("order_created", **{"order.id": "o-1", "order.total": 14.5, "cart_size": 2}),
    _line("order_created", **{"order.id": "o-2", "order.total": 3, "cart_size": 1}),
    _line("payment_done", status=200, **{"order.id": "o-1"}),                # synonym
    _line("request_done", http_route="/checkout/v7"),                                   # underscore semconv
    _line("confirmation_sent", email="anna@example.com", **{"order.id": "o-1"}),  # sensitive
    _line("login", password="hunter2"),                                       # sensitive key
    _line("flag_set", **{"feature.enabled": True}),
])


def _json(cwd, *args):
    r = run_cli("check", "logs.jsonl", "--json", *args, cwd=cwd)
    assert r.returncode in (0, 1), r.stderr
    return json.loads(r.stdout)


def _unknown(res):
    return sum(r["count"] for r in res["by_rule"] if r["rule"] == "missing_attribute")


@pytest.mark.weaver
def test_claim1_declares_custom_fields_only(tmp_path):
    (tmp_path / "logs.jsonl").write_text(LOGS, encoding="utf-8")
    r = run_cli("init", "--from", "logs.jsonl", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "declared 4 new fields" in r.stdout and "4 to fix in the code" in r.stdout
    text = (tmp_path / "logwarden-fields.yaml").read_text(encoding="utf-8")
    from logwarden import fields
    assert fields.declared(text) >= {"order.id", "order.total", "cart_size", "feature.enabled"}
    for name in ("status", "http_route", "email", "password"):
        assert f"- id: {name}\n" not in text
    assert "#   status: adr_banned_synonym" in text and "#   email: adr_sensitive_data" in text
    assert "#   http_route: adr_semconv_prefix" in text
    assert "type: double" in text.split("id: order.total")[1].split("- id:")[0]   # 14.5 and 3
    assert "type: int" in text.split("id: cart_size")[1].split("- id:")[0]
    assert "type: boolean" in text.split("id: feature.enabled")[1].split("- id:")[0]
    assert "Seen on 4 of 7 lines" in text.split("id: order.id")[1].split("- id:")[0]
    assert "A bare name" in text.split("id: cart_size")[1].split("- id:")[0]
    assert "A bare name" not in text.split("id: order.id")[1].split("- id:")[0]
    for value in ("o-1", "o-2", "14.5", "anna@example.com", "hunter2", "/checkout/v7"):
        assert value not in text                                            # no logged values
    assert (tmp_path / "logwarden.toml").read_text(encoding="utf-8") == 'profile = "standard"\n'
    res = _json(tmp_path)
    assert res["profile"] == "standard"
    # only the field that a rule wants gone is still unknown
    assert {f["field"] for f in res["findings"] if f["rule"] == "missing_attribute"} == {"email"}
    rules = {r["rule"] for r in res["by_rule"]}
    assert {"adr_banned_synonym", "adr_semconv_prefix", "adr_sensitive_data"} <= rules   # still reported


@pytest.mark.weaver
def test_claim1_rerun_adds_and_keeps_edits(tmp_path):
    (tmp_path / "logs.jsonl").write_text(LOGS, encoding="utf-8")
    run_cli("init", "--from", "logs.jsonl", cwd=tmp_path)
    f = tmp_path / "logwarden-fields.yaml"
    edited = f.read_text(encoding="utf-8").replace(
        '"TODO: what it is. Seen on 4 of 7 lines."', "Order id from the orders service.", 1)
    f.write_text(edited, encoding="utf-8")
    (tmp_path / "logwarden.toml").write_text('profile = "essential"\nfields = "logwarden-fields.yaml"\n'
                                             '[source]\nformat = "jsonl"\n', encoding="utf-8")
    (tmp_path / "logs.jsonl").write_text(LOGS + _line("shipped", carrier_code="dhl") + "\n", encoding="utf-8")
    r = run_cli("init", "--from", "logs.jsonl", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "declared 1 new field\n" in r.stdout
    text = f.read_text(encoding="utf-8")
    assert text.startswith(edited.rstrip("\n").split("\n#   status")[0][:200])
    assert "Order id from the orders service." in text and text.count("- id: order.id") == 1
    assert "- id: carrier_code" in text
    assert text.count("#   status: adr_banned_synonym") == 1                 # not listed twice
    toml = (tmp_path / "logwarden.toml").read_text(encoding="utf-8")
    assert toml.startswith('profile = "standard"\n') and '[source]\nformat = "jsonl"' in toml
    assert "carrier_code" not in {x["field"] for x in _json(tmp_path)["findings"]}


@pytest.mark.weaver
def test_claim1_template_and_pyproject(tmp_path):
    (tmp_path / "logs.jsonl").write_text(LOGS, encoding="utf-8")
    assert run_cli("init", cwd=tmp_path).returncode == 0                   # the empty template
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n\n[tool.logwarden]\nupstreams = ["a.b"]\n'
                                             '\n[tool.other]\nprofile = "keep"\n', encoding="utf-8")
    r = run_cli("init", "--from", "logs.jsonl", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "logwarden-fields.yaml").read_text(encoding="utf-8")
    assert "groups: []" not in text and "- id: order.id" in text
    assert text.startswith("# Project-only log fields")                        # the template's comment kept
    py = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.logwarden]\nprofile = "standard"\nupstreams = ["a.b"]' in py
    assert '[tool.other]\nprofile = "keep"' in py
    assert _json(tmp_path)["profile"] == "standard"


@pytest.mark.weaver
def test_claim1_exports_as_input(tmp_path):
    hits = [{"_id": str(i), "_source": {"@timestamp": "x", "log": ln}} for i, ln in enumerate(LOGS.splitlines())]
    (tmp_path / "es.json").write_text(json.dumps({"hits": {"total": {"value": len(hits)}, "hits": hits}}),
                                      encoding="utf-8")
    r = run_cli("init", "--from", "es.json", "--message-field", "log", cwd=tmp_path)
    assert r.returncode == 0 and "declared 4 new fields" in r.stdout, r.stdout + r.stderr
    assert run_cli("init", "--from", "nope.jsonl", cwd=tmp_path).returncode == 2


@pytest.mark.weaver
def test_claim2_ready_for_standard(tmp_path):
    (tmp_path / "logs.jsonl").write_text(LOGS, encoding="utf-8")
    ess = _json(tmp_path, "--profile", "essential")
    std = _json(tmp_path, "--profile", "standard")
    u = ess["outside_profile_unknown_fields"]
    assert ess["outside_profile"] == std["violations"] - ess["violations"]
    assert u > 0 and ess["outside_profile"] - u > 0
    text = run_cli("check", "logs.jsonl", "--profile", "essential", cwd=tmp_path).stdout
    assert f"{u} of them are unknown fields, which `logwarden init --from LOGS` declares" in text
    before = _unknown(std)
    run_cli("init", "--from", "logs.jsonl", cwd=tmp_path)
    after = _unknown(_json(tmp_path, "--profile", "standard"))
    assert before - after == u                                              # exactly what init declared


def test_claim4_update_pins_rewrites(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import update_pins as up
    for name in ("WEAVER_PY", "MANIFEST", "GUIDE"):
        src = getattr(up, name)
        dst = tmp_path / src.name
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(up, name, dst)
    monkeypatch.setattr(up, "_get", lambda url: b"archive-bytes")
    monkeypatch.setattr(up, "latest", lambda repo: {"weaver": "v0.28.0", "semantic-conventions": "v1.45.0"}[repo])
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert up.main([]) == 0                                                 # current: nothing to do
    assert "changed=false" in out.read_text()
    up.main(["--weaver", "v0.29.0", "--semconv", "1.46.0"])
    import hashlib
    sha = hashlib.sha256(b"archive-bytes").hexdigest()
    w = up.WEAVER_PY.read_text(encoding="utf-8")
    assert '"WEAVER_VERSION", "v0.29.0"' in w and f'"1.46.0": "{sha}"' in w and '"1.45.0":' in w
    m = up.MANIFEST.read_text(encoding="utf-8")
    assert "schemas/1.46.0" in m and "semantic-conventions@v1.46.0[model]" in m
    g = up.GUIDE.read_text(encoding="utf-8")
    assert "Weaver binary (v0.29.0)" in g and "semantic-conventions v1.46.0" in g
    assert "changed=true\ntitle=Pins: Weaver v0.28.0 → v0.29.0, semconv 1.45.0 → 1.46.0" in out.read_text()
