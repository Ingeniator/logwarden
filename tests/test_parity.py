"""v0.3.0 claim 1: the Python CLI gives the same result as the v0.2.0 bash CLI.

tests/golden/*.json and *.txt were produced by v0.2.0 (bin/logwarden, commit 6740972).
"""
import json

import pytest

from conftest import DATA, EXAMPLES, GOLDEN

CASES = {"good": EXAMPLES / "good.jsonl", "bad": EXAMPLES / "bad.jsonl", "mixed": DATA / "mixed.jsonl"}


def _strip(result):
    result = dict(result)
    result.pop("version")
    return result


@pytest.mark.weaver
@pytest.mark.parametrize("name", CASES)
def test_json_matches_v020(cli, name, tmp_path):
    r = cli("check", CASES[name], "--json", cwd=tmp_path)
    golden = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
    assert r.returncode == (0 if golden["ok"] else 1), r.stderr
    assert _strip(json.loads(r.stdout)) == _strip(golden)


@pytest.mark.weaver
@pytest.mark.parametrize("name", CASES)
def test_text_matches_v020(cli, name, tmp_path):
    r = cli("check", CASES[name], "--max", "1000", cwd=tmp_path)
    assert r.stdout == (GOLDEN / f"{name}.txt").read_text(encoding="utf-8")
