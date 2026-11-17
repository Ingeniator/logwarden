"""Unit tests for log line parsing and Weaver sample conversion (no weaver needed)."""
from logwarden.check import parse_lines, to_sample


def test_parse_lines_keeps_line_numbers_and_counts_all_lines():
    text = 'x\n{"a":1}\n\n{"b":2}\n[1]\n{"c":3}'
    records, total = parse_lines(text)
    assert [(r.line, r.obj) for r in records] == [(2, {"a": 1}), (4, {"b": 2}), (6, {"c": 3})]
    assert total == 6


def test_parse_lines_splits_on_newline_only():
    records, total = parse_lines('{"a":"x y"}\r\n{"b":1}\n')
    assert total == 2 and len(records) == 2


def test_to_sample_types_and_values():
    s = to_sample({"level": "WARNING", "event": "e", "trace_id": "t", "ok": True, "n": 2, "f": 1.5,
                   "obj": {"a": [1]}, "none": None, "s": "x", "timestamp": "ts"})["log"]
    assert s["severity_number"] == 13 and s["severity_text"] == "WARNING" and s["body"] == "e"
    assert s["trace_id"] == "t" and s["span_id"] is None and s["event_name"] == ""
    attrs = {a["name"]: (a["type"], a["value"]) for a in s["attributes"]}
    assert attrs == {"ok": ("boolean", True), "n": ("int", 2), "f": ("double", 1.5),
                     "obj": ("string", '{"a":[1]}'), "none": ("string", ""), "s": ("string", "x")}


def test_to_sample_defaults():
    s = to_sample({})["log"]
    assert s["severity_number"] == 9 and s["severity_text"] == "" and s["body"] == ""
