"""v0.10.2: fields logged as lists are checked as string[], int[], double[] or boolean[]."""
import json

import pytest

from conftest import run_cli
from logwarden import fields
from logwarden.check import to_sample
from test_new_rules import _line

FIELDS = """groups:
  - id: registry.project.log
    type: attribute_group
    brief: List fields.
    attributes:
      - id: tags
        type: string[]
        brief: Tags.
        stability: development
      - id: counts
        type: int[]
        brief: Counts.
        stability: development
      - id: ratios
        type: double[]
        brief: Ratios.
        stability: development
      - id: flags
        type: boolean[]
        brief: Flags.
        stability: development
"""


def _attrs(obj, declared=None):
    return {a["name"]: (a["type"], a["value"]) for a in to_sample(obj, declared)["log"]["attributes"]}


def test_lists_of_one_type_are_arrays():
    assert _attrs({"tags": ["a", "b"], "counts": [1, 2], "ratios": [0.5, 1.5], "mixed_num": [1, 2.5],
                   "flags": [True, False]}) == {
        "tags": ("string[]", ["a", "b"]), "counts": ("int[]", [1, 2]), "ratios": ("double[]", [0.5, 1.5]),
        "mixed_num": ("double[]", [1, 2.5]), "flags": ("boolean[]", [True, False])}


def test_other_lists_stay_json_text():
    assert _attrs({"mixed": [1, "x"], "bools_and_ints": [True, 1], "with_null": ["a", None],
                   "nested": [["a"]], "objects": [{"a": 1}], "empty": []}) == {
        "mixed": ("string", '[1,"x"]'), "bools_and_ints": ("string", "[true,1]"),
        "with_null": ("string", '["a",null]'), "nested": ("string", '[["a"]]'),
        "objects": ("string", '[{"a":1}]'), "empty": ("string", "[]")}


def test_empty_list_takes_the_declared_array_type():
    declared = {"counts": "int[]", "name": "string"}
    assert _attrs({"counts": [], "name": [], "other": []}, declared) == {
        "counts": ("int[]", []), "name": ("string", "[]"), "other": ("string", "[]")}


def test_init_from_proposes_array_types():
    assert fields._type([["a"], ["b", "c"], []]) == "string[]"
    assert fields._type([[1], [2.5]]) == "double[]"
    assert fields._type([[], []]) == "string[]"
    assert fields._type([[True]]) == "boolean[]"


LINES = [
    _line("tagged", tags=["a", "b"], counts=[1, 2], ratios=[0.5, 2], flags=[True]),
    _line("tagged", tags=[], counts=[], ratios=[], flags=[]),
    _line("tagged", **{"process.command_args": ["python", "-m", "app"]}),   # semconv string[]
]


def _check(tmp_path, lines, *args):
    (tmp_path / "logwarden-fields.yaml").write_text(FIELDS, encoding="utf-8")
    (tmp_path / "logs.jsonl").write_text("".join(x + "\n" for x in lines), encoding="utf-8")
    r = run_cli("check", "logs.jsonl", "--profile", "standard", "--json", *args, cwd=tmp_path)
    assert r.returncode in (0, 1), r.stderr
    return json.loads(r.stdout)


@pytest.mark.weaver
def test_declared_list_fields_pass(tmp_path):
    res = _check(tmp_path, LINES)
    assert res["violations"] == 0, res["findings"]


@pytest.mark.weaver
def test_wrong_list_types_fail(tmp_path):
    res = _check(tmp_path, [_line("tagged", counts=[1, 2.5]), _line("tagged", tags="a"),
                            _line("tagged", tags=[1, "x"])])
    got = [(f["line"], f["rule"], f["field"]) for f in res["findings"]]
    assert got == [(1, "type_mismatch", "counts"), (2, "type_mismatch", "tags"),
                   (3, "type_mismatch", "tags")], res["findings"]


@pytest.mark.weaver
def test_secrets_inside_lists_are_found(tmp_path):
    secret = [_line("tagged", tags=["ok", "someone@example.com"]),
              _line("tagged", tags=["4111 1111 1111 1111"]),
              _line("tagged", counts=[4111111111111111]),
              _line("tagged", tags=["Bearer abcdefghijklmnopqrstuvwxyz"]),
              _line("tagged", password=["***"]),
              _line("tagged", counts=[4111, 1111, 1111, 1111])]   # items never join into a card
    res = _check(tmp_path, secret)
    got = [(f["line"], f["field"]) for f in res["findings"] if f["rule"] == "adr_sensitive_data"]
    assert got == [(1, "tags"), (2, "tags"), (3, "counts"), (4, "tags"), (5, "password")], res["findings"]
    assert "example.com" not in json.dumps(res) and "4111" not in json.dumps(res)


@pytest.mark.weaver
def test_init_from_declares_a_list_field(tmp_path):
    (tmp_path / "logs.jsonl").write_text(_line("stored", stored_to=["s3", "pg"]) + "\n"
                                         + _line("stored", stored_to=[]) + "\n", encoding="utf-8")
    r = run_cli("init", "--from", "logs.jsonl", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "logwarden-fields.yaml").read_text(encoding="utf-8")
    assert "type: string[]" in text.split("id: stored_to")[1].split("- id:")[0]
    r = run_cli("check", "logs.jsonl", "--json", cwd=tmp_path)
    res = json.loads(r.stdout)
    assert r.returncode == 0 and res["violations"] == 0, res["findings"]
