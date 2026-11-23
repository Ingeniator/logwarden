"""v0.10.2: timestamp ids aren't card numbers; a baselined secret kind doesn't hide another kind.

All values here are synthetic: public test card numbers and a made-up JWT.
"""
import json

import pytest

from conftest import run_cli
from test_new_rules import _line

# Luhn-valid, so only the issuer prefix/length and the grouping rule them out.
NOT_CARDS = ["20260911-120000", "ui_draft/20261122-155600-0003", "20261122155009",
             "order 4111-111111-11111-1", "ref 41111111 11111111"]
CARDS = ["4111111111111111", "4111 1111 1111 1111", "4111-1111-1111-1111", "5555555555554444",
         "2223003122003222", "378282246310005", "3782 822463 10005", "6011111111111117",
         "30569309025904", "3530111333300000", "pan=4012888888881881"]
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.c3ludGhldGljLXNpZ25hdHVyZQ"


def _check(tmp_path, text, *args):
    r = run_cli("check", "-", "--json", *args, stdin=text, cwd=tmp_path)
    return json.loads(r.stdout)


def _cards(res):
    return [f for f in res["findings"] if "card number" in (f["message"] or "")]


@pytest.mark.weaver
def test_timestamp_ids_are_not_cards_and_cards_still_are(tmp_path):
    lines = [_line("not_card", ref=v) for v in NOT_CARDS] + [_line("card", ref=v) for v in CARDS]
    res = _check(tmp_path, "\n".join(lines) + "\n")
    flagged = {f["line"] for f in _cards(res)}
    assert flagged == set(range(len(NOT_CARDS) + 1, len(lines) + 1))


@pytest.mark.weaver
def test_baselined_card_on_a_field_does_not_hide_a_jwt_there(tmp_path):
    old = _line("Request completed", query_params="card=4111111111111111") + "\n"
    (tmp_path / "logs.jsonl").write_text(old)
    assert run_cli("baseline", "logs.jsonl", "--output", "b.json", cwd=tmp_path).returncode == 0
    entries = json.loads((tmp_path / "b.json").read_text())["entries"]
    assert [e.get("kind") for e in entries if e["rule"] == "adr_sensitive_data"] == ["card_number"]

    same = _check(tmp_path, old, "--baseline", "b.json")
    assert same["ok"] and same["baselined"] >= 1

    planted = old + _line("Request completed", query_params=f"token={JWT}") + "\n"
    res = _check(tmp_path, planted, "--baseline", "b.json")
    # Line 2 repeats line 1's findings except the card (baselined); only the JWT is new.
    assert not res["ok"] and res["baselined"] == 2 * same["baselined"] - 1
    [f] = res["findings"]
    assert f["line"] == 2 and "a JWT" in f["message"]
    assert JWT not in json.dumps(res)          # the value is never printed


@pytest.mark.weaver
def test_secret_entries_from_older_baselines_hide_nothing(tmp_path):
    old = _line("Request completed", query_params="card=4111111111111111") + "\n"
    (tmp_path / "b.json").write_text(json.dumps({
        "tool": "logwarden", "format": 1, "entries": [
            {"rule": "adr_sensitive_data", "field": "query_params", "logger": "app.main",
             "event": "Request completed", "count": 1}]}))
    res = _check(tmp_path, old, "--baseline", "b.json")
    assert not res["ok"] and res["baselined"] == 0
    assert res["baseline_outdated"] == 1 and res["baseline_fixed"] == 0
    text = run_cli("check", "-", "--baseline", "b.json", stdin=old, cwd=tmp_path).stdout
    assert "before v0.10.2" in text and "logwarden baseline" in text
