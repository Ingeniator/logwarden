"""Input formats and record unwrapping.

- jsonl: one JSON object per line (also Elasticsearch documents with `_source`, e.g. elasticdump)
- es: an Elasticsearch `_search` response, several scroll pages (JSON array) or one per line
- csv: a Kibana Discover CSV report (a `_source`/`log`/`message` column with the whole line,
  or one column per field; values get their types back from the registry)

`[source]` in the config unwraps what a log shipper such as Fluent Bit added: the service's
line as a string (or object) in `message_field`, and pipeline fields in `ignore_fields`.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

FORMATS = ("auto", "jsonl", "es", "csv")
REQUIRED = ("service.name", "service.version", "deployment.environment.name")
NO_SERVICE = "(none)"
_ES_META = {"_id", "_index", "_score", "_type", "_routing", "_ignored", "sort", "highlight"}
_HEADER = re.compile(r"^[@A-Za-z_][\w.@/-]*$")
_GROUPED = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_INT = re.compile(r"^-?\d+$")


class SourceError(ValueError):
    """The input can't be read in the given format (CLI exit code 2)."""


@dataclass
class Source:
    """How to read the input: the [source] config section plus the command-line options."""
    format: str = "auto"
    message_field: str | None = None
    ignore_fields: list[str] = field(default_factory=list)
    service_field: str = "service.name"
    services: list[str] | None = None        # --service: check only these


@dataclass
class Item:
    """One input record before parsing: a JSON object, or text that isn't one."""
    n: int                     # line, CSV row or search hit number (1-based)
    obj: dict | None
    text: str | None = None    # set when obj is None
    kind: str = "plain text"
    id: str | None = None      # Elasticsearch _id
    file_line: int | None = None   # line in the input file, when it has one


@dataclass
class Loaded:
    items: list[Item]
    total: int
    format: str
    missing_columns: list[str] = field(default_factory=list)


def _json_kind(v: Any) -> str:
    if isinstance(v, list):
        return "JSON array"
    if isinstance(v, str):
        return "JSON string"
    if isinstance(v, bool) or v is None:
        return "JSON literal"
    return "JSON number"


def _is_response(v: Any) -> bool:
    return isinstance(v, dict) and isinstance(v.get("hits"), dict) and "hits" in v["hits"]


def detect(text: str) -> str:
    s = text.lstrip("﻿ \t\r\n")
    if not s:
        return "jsonl"
    first = s.split("\n", 1)[0].strip()
    if s[0] in "{[":
        try:
            doc = json.loads(s)
        except ValueError:
            doc = None
        if _is_response(doc) or (isinstance(doc, list) and doc and all(_is_response(d) for d in doc)):
            return "es"
        try:
            if _is_response(json.loads(first)):
                return "es"
        except ValueError:
            pass
        return "jsonl"
    try:
        header = next(csv.reader([first]))
    except (csv.Error, StopIteration):
        return "jsonl"
    whole_line = len(header) == 1 and header[0].strip() in ("_source", "log", "message")
    if (len(header) >= 2 or whole_line) and all(_HEADER.match(h.strip()) for h in header):
        return "csv"
    return "jsonl"


def _jsonl(text: str) -> Loaded:
    # Split on "\n" only, like `jq -R` (str.splitlines also splits on \x0b,  , ...).
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    items = []
    for n, raw in enumerate(lines, start=1):
        try:
            obj = json.loads(raw)
        except ValueError:
            if raw.strip():
                items.append(Item(n, None, raw, "plain text", file_line=n))
            continue
        if isinstance(obj, dict):
            doc_id = None
            if isinstance(obj.get("_source"), dict) and ("_id" in obj or "_index" in obj):
                doc_id, obj = _str_or_none(obj.get("_id")), obj["_source"]
            items.append(Item(n, obj, id=doc_id, file_line=n))
        else:
            items.append(Item(n, None, raw, _json_kind(obj), file_line=n))
    return Loaded(items, len(lines), "jsonl")


def _str_or_none(v: Any) -> str | None:
    return None if v is None else str(v)


def _hit_obj(hit: dict) -> dict | None:
    if isinstance(hit.get("_source"), dict):
        return hit["_source"]
    if isinstance(hit.get("fields"), dict):   # a search with "fields" and no _source
        return {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in hit["fields"].items()}
    return None


def _es(text: str) -> Loaded:
    s = text.lstrip("﻿")
    try:
        doc = json.loads(s)
        pages = doc if isinstance(doc, list) else [doc]
    except ValueError:
        try:
            pages = [json.loads(ln) for ln in s.split("\n") if ln.strip()]
        except ValueError as e:
            raise SourceError(f"not an Elasticsearch _search response: {e}") from e
    if not pages or not all(_is_response(p) for p in pages):
        raise SourceError("not an Elasticsearch _search response (no hits.hits); "
                          "use --format jsonl or csv for other exports")
    items, n = [], 0
    for page in pages:
        for hit in page["hits"]["hits"] or []:
            n += 1
            obj = _hit_obj(hit) if isinstance(hit, dict) else None
            if obj is None:
                items.append(Item(n, None, json.dumps(hit)[:200], "search hit without _source"))
            else:
                items.append(Item(n, obj, id=_str_or_none(hit.get("_id"))))
    return Loaded(items, n, "es")


def _unescape(v: str) -> str:
    # Kibana prefixes cells that start with = + - @ with ' (formula escaping, csv.escapeFormulaValues).
    if len(v) > 1 and v[0] == "'" and v[1] in "=+-@\t\r":
        return v[1:]
    return v


def _coerce(v: str, typ: str | None) -> Any:
    if typ == "int":
        t = v.replace(",", "") if _GROUPED.match(v) else v
        return int(t) if _INT.match(t) else v
    if typ == "double":
        t = v.replace(",", "") if _GROUPED.match(v) else v
        try:
            f = float(t)
            return f if math.isfinite(f) else v
        except ValueError:
            return v
    if typ == "boolean" and v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v


def _json_object(v: str) -> dict | None:
    if not v.lstrip().startswith("{"):
        return None
    try:
        obj = json.loads(v)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _csv(text: str, src: Source, types: Callable[[], dict[str, str]]) -> Loaded:
    reader = csv.reader(io.StringIO(text.lstrip("﻿"), newline=""))
    try:
        header = [_unescape(h.strip()) for h in next(reader)]
    except StopIteration:
        return Loaded([], 0, "csv")
    except csv.Error as e:
        raise SourceError(f"not a CSV file: {e}") from e
    whole = [c for c in ("_source", *([src.message_field] if src.message_field else ["log", "message"]))
             if c in header]
    typemap: dict[str, str] | None = None
    items, columns_used = [], False
    try:
        for n, row in enumerate(reader, start=1):
            if not any(cell.strip() for cell in row):
                continue
            cells = {h: _unescape(row[i]) for i, h in enumerate(header) if i < len(row)}
            obj = next((o for c in whole if (o := _json_object(cells.get(c, ""))) is not None), None)
            doc_id = cells.get("_id") or None
            if obj is None:
                columns_used = True
                if typemap is None:
                    typemap = types()
                obj = {h: _coerce(v, typemap.get(h)) for h, v in cells.items()
                       if v != "" and h not in _ES_META}
            items.append(Item(n, obj, id=doc_id, file_line=reader.line_num))
    except csv.Error as e:
        raise SourceError(f"CSV row {reader.line_num}: {e}") from e
    missing = []
    if columns_used:
        present = {h for h in header if not ignored(h, src.ignore_fields)}
        missing = [f for f in REQUIRED if f not in present]
    return Loaded(items, len(items), "csv", missing)


def ignored(key: str, patterns: list[str]) -> bool:
    return any(key == p or key.startswith(p + ".") for p in patterns)


def unwrap(item: Item, src: Source) -> Item:
    """Take the service's line out of `message_field` and drop `ignore_fields`."""
    if item.obj is None:
        return item
    obj = item.obj
    mf = src.message_field
    if mf and mf in obj:
        v = obj[mf]
        if isinstance(v, str):
            try:
                inner = json.loads(v)
            except ValueError:
                return Item(item.n, None, v, "plain text", item.id, item.file_line)
            if not isinstance(inner, dict):
                return Item(item.n, None, v, _json_kind(inner), item.id, item.file_line)
            obj = inner
        elif isinstance(v, dict):
            obj = v
    if src.ignore_fields:
        obj = {k: v for k, v in obj.items() if not ignored(k, src.ignore_fields)}
    return Item(item.n, obj, None, item.kind, item.id, item.file_line)


def service_of(item: Item, src: Source) -> str:
    v = item.obj.get(src.service_field) if item.obj is not None else None
    return v if isinstance(v, str) and v else NO_SERVICE


def load(text: str, src: Source | None = None, types: Callable[[], dict[str, str]] = dict) -> Loaded:
    """Read `text` in src.format (or detect it), unwrap records and apply --service."""
    src = src or Source()
    fmt = detect(text) if src.format == "auto" else src.format
    if fmt not in FORMATS[1:]:
        raise SourceError(f"unknown format '{fmt}' (one of {', '.join(FORMATS)})")
    loaded = _jsonl(text) if fmt == "jsonl" else _es(text) if fmt == "es" else _csv(text, src, types)
    loaded.items = [unwrap(i, src) for i in loaded.items]
    if src.services:
        keep = set(src.services)
        loaded.items = [i for i in loaded.items if service_of(i, src) in keep]
        loaded.total = len(loaded.items)
    return loaded
