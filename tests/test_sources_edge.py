"""Malformed and unusual exports: what sources.load accepts, and the errors it gives for the rest."""
import csv
import json

import pytest

from logwarden import sources
from logwarden.sources import Source, SourceError, detect, load

LINE = {"event": "order_created", "service.name": "api"}


def _page(*hits):
    return {"hits": {"total": {"value": len(hits)}, "hits": list(hits)}}


@pytest.mark.parametrize("text,fmt", [
    ("", "jsonl"),
    ("﻿  \n", "jsonl"),
    ('{"broken": \n', "jsonl"),                       # starts like JSON, isn't: read as lines
    (json.dumps(_page()) + "\n" + json.dumps(_page()) + "\n", "es"),   # NDJSON pages
    ("plain text line\n", "jsonl"),                   # one column that isn't a known header
    ("not a header, at all\n", "jsonl"),
    ('"unterminated\n', "jsonl"),
    ("﻿_source\n", "csv"),                       # BOM and a single whole-line column
    ("@timestamp,service.name\n", "csv"),
])
def test_detect(text, fmt):
    assert detect(text) == fmt


def test_search_hits_with_fields_or_nothing():
    text = json.dumps(_page(
        {"_id": "a", "fields": {"event": ["order_created"], "service.name": ["api"], "tags": ["x", "y"]}},
        {"_id": "b"},                                  # neither _source nor fields
        "not even an object",
    ))
    items = load(text).items
    assert items[0].obj == {"event": "order_created", "service.name": "api", "tags": ["x", "y"]}
    assert items[0].id == "a"
    assert [i.kind for i in items[1:]] == ["search hit without _source"] * 2
    assert items[1].obj is None and '"_id": "b"' in items[1].text


def test_empty_search_response():
    loaded = load(json.dumps({"hits": {"hits": None}}), Source(format="es"))
    assert (loaded.items, loaded.total) == ([], 0)


@pytest.mark.parametrize("text,msg", [
    ('{"took": 1}', "no hits.hits"),
    ("[]", "no hits.hits"),
    ('{"hits": {"hits": []}}\n{not json\n', "not an Elasticsearch _search response"),
])
def test_bad_search_exports(text, msg):
    with pytest.raises(SourceError, match=msg):
        load(text, Source(format="es"))


def test_unknown_format():
    with pytest.raises(SourceError, match="unknown format 'xml'"):
        load("{}", Source(format="xml"))


def test_csv_empty_blank_rows_and_bom():
    assert load("", Source(format="csv")).items == []
    loaded = load('﻿event,service.name\n,\norder_created,api\n\n', Source(format="csv"))
    assert [i.obj for i in loaded.items] == [{"event": "order_created", "service.name": "api"}]
    assert loaded.items[0].n == 2                      # row number counts the blank row


def test_csv_too_large_field():
    old = csv.field_size_limit(8)
    try:
        with pytest.raises(SourceError, match="CSV row 2"):
            load("e,s\n" + "x" * 50 + ",api\n", Source(format="csv"))
        with pytest.raises(SourceError, match="not a CSV file"):
            load("x" * 50 + ",y\n", Source(format="csv"))
    finally:
        csv.field_size_limit(old)


def test_csv_types():
    types = {"n": "int", "d": "double", "b": "boolean"}
    text = ("n,d,b\n"
            '"1,234","1,234.5",TRUE\n'
            "12x,abc,yes\n"
            "-7,inf,false\n")
    rows = [i.obj for i in load(text, Source(format="csv"), types=lambda: types).items]
    assert rows[0] == {"n": 1234, "d": 1234.5, "b": True}
    assert rows[1] == {"n": "12x", "d": "abc", "b": "yes"}       # not that type: kept as text
    assert rows[2] == {"n": -7, "d": "inf", "b": False}          # inf isn't a finite double


def test_csv_formula_escaping_and_json_like_cells():
    text = "event,log,note\n" "order_created,{not json,'=SUM(A1)\n" "order_created,'-1,'x\n"
    rows = [i.obj for i in load(text, Source(format="csv")).items]
    assert rows[0] == {"event": "order_created", "log": "{not json", "note": "=SUM(A1)"}
    assert rows[1] == {"event": "order_created", "log": "-1", "note": "'x"}


@pytest.mark.parametrize("value,kind", [('["a"]', "JSON array"), ('"s"', "JSON string"),
                                        ("3", "JSON number"), ("null", "JSON literal")])
def test_message_field_that_is_json_but_not_an_object(value, kind):
    line = json.dumps({"log": value, "stream": "stdout"})
    item = load(line, Source(message_field="log")).items[0]
    assert (item.obj, item.text, item.kind) == (None, value, kind)


def test_message_field_object_and_absent():
    lines = json.dumps({"log": LINE, "kubernetes": {"pod": "p"}}) + "\n" + json.dumps({"other": 1})
    items = load(lines, Source(message_field="log", ignore_fields=["kubernetes"])).items
    assert items[0].obj == LINE
    assert items[1].obj == {"other": 1}                # no message_field: the record as is


def test_service_filter_with_custom_field():
    lines = "\n".join(json.dumps({"event": "e", "app": a}) for a in ("a", "b", "")) + "\nplain\n"
    src = Source(service_field="app", services=["a", "(none)"])
    loaded = load(lines, src)
    assert [sources.service_of(i, src) for i in loaded.items] == ["a", "(none)", "(none)"]
    assert loaded.total == 3
