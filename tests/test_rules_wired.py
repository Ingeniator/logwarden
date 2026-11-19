"""v0.4.0 claim 9: every rule is documented, has a fix hint and is exercised by examples/bad.jsonl."""
import json
import re

import pytest

from conftest import EXAMPLES, ROOT
from logwarden import check, data

WEAVER_BUILTIN = {"missing_attribute", "deprecated", "type_mismatch"}


def _own_rules():
    rego = data.path("policies/logwarden.rego").read_text(encoding="utf-8")
    return set(re.findall(r'make_advice\("([a-z_]+)"', rego)) - {"advice_type"} | {check.NON_JSON, check.UPSTREAM, check.EVENT_CATALOG}


def test_every_rule_documented():
    rules = data.rules()
    doc = (ROOT / "docs/rules.md").read_text(encoding="utf-8")
    standard = (ROOT / "docs/standard.md").read_text(encoding="utf-8")
    for rule in _own_rules() | WEAVER_BUILTIN:
        assert rule in rules, rule
        assert {"section", "title", "hint", "doc"} <= set(rules[rule]), rule
        assert re.search(rf"^## {rule}$", doc, re.M), rule
    for rule in _own_rules():
        assert f"(rules.md#{rule})" in standard, rule
    assert set(rules) == _own_rules() | WEAVER_BUILTIN


@pytest.mark.weaver
def test_bad_example_triggers_every_own_rule(cli, tmp_path):
    res = json.loads(cli("check", EXAMPLES / "bad.jsonl", "--json", cwd=tmp_path).stdout)
    # Upstream coverage and the event catalog need config; test_upstreams.py and
    # test_review.py cover them.
    assert _own_rules() - {check.UPSTREAM, check.EVENT_CATALOG} <= {r["rule"] for r in res["by_rule"]}
