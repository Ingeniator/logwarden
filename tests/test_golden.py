"""Full CLI output for the example files matches the reviewed goldens in tests/golden/.

History: the goldens were first produced by the v0.2.0 bash CLI (the v0.3.0 Python port
matched them exactly). They are regenerated on purpose when output changes, with the
diff reviewed and the reason given in the release notes. Regenerate with `make golden`.
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
def test_json_matches_golden(cli, name, tmp_path):
    r = cli("check", CASES[name], "--json", cwd=tmp_path)
    golden = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
    assert r.returncode == (0 if golden["ok"] else 1), r.stderr
    assert _strip(json.loads(r.stdout)) == _strip(golden)


@pytest.mark.weaver
@pytest.mark.parametrize("name", CASES)
def test_text_matches_golden(cli, name, tmp_path):
    r = cli("check", CASES[name], "--max", "1000", cwd=tmp_path)
    assert r.stdout == (GOLDEN / f"{name}.txt").read_text(encoding="utf-8")
