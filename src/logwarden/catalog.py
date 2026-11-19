"""Event catalog: which event names a run emits, and the committed catalog file (TOML).

The file is written by `logwarden catalog -o` and read back with tomllib, so the core
install needs no YAML parser:

    [events.order_imported]
    description = "An order file was parsed and stored."
    levels = ["info"]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

# Fields every line carries; listing them per event says nothing.
COMMON = {"timestamp", "level", "event", "logger", "trace_id", "span_id",
          "service.name", "service.version", "deployment.environment.name"}
_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_HEADER = ("# Event catalog: the event names this service logs (logwarden standard §4).\n"
           "# Describe each event. 'logwarden catalog LOGS -o FILE' adds new ones and keeps the rest.\n"
           "# With  events = \"<this file>\"  in the logwarden config, check reports events not listed here.\n")


class CatalogError(ValueError):
    pass


def collect(records) -> dict[str, dict]:
    """Per string event: count, levels, loggers, fields (beyond the common ones), first line."""
    out: dict[str, dict] = {}
    for r in records:
        ev = r.obj.get("event")
        if not isinstance(ev, str) or not ev:
            continue
        e = out.setdefault(ev, {"count": 0, "levels": set(), "loggers": set(), "fields": set(),
                                "first_line": r.line})
        e["count"] += 1
        if isinstance(r.obj.get("level"), str):
            e["levels"].add(r.obj["level"].lower())
        if isinstance(r.obj.get("logger"), str):
            e["loggers"].add(r.obj["logger"])
        e["fields"].update(k for k in r.obj if k not in COMMON)
    return {k: {**v, "levels": sorted(v["levels"]), "loggers": sorted(v["loggers"]),
                "fields": sorted(v["fields"])} for k, v in sorted(out.items())}


def load(path: Path) -> dict[str, dict]:
    """The catalog file's events (name -> table). Raises CatalogError."""
    try:
        content = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise CatalogError(f"can't read event catalog {path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise CatalogError(f"event catalog {path} is not valid TOML: {e}") from e
    events = content.get("events", {})
    if not isinstance(events, dict) or not all(isinstance(v, dict) for v in events.values()):
        raise CatalogError(f"event catalog {path}: expected [events.<name>] tables")
    return events


def _key(name: str) -> str:
    return name if _BARE.match(name) else json.dumps(name, ensure_ascii=False)


def _str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # JSON string escapes are valid TOML basic strings


def merge(existing: dict[str, dict], seen: dict[str, dict]) -> dict[str, dict]:
    """Keep every existing event and its description; add new events; union the levels."""
    out = {k: dict(v) for k, v in existing.items()}
    for name, s in seen.items():
        e = out.setdefault(name, {"description": ""})
        levels = e.get("levels") if isinstance(e.get("levels"), list) else []
        e["levels"] = sorted({*map(str, levels), *s["levels"]})
    return dict(sorted(out.items()))


def dumps(events: dict[str, dict]) -> str:
    parts = [_HEADER]
    for name, e in events.items():
        lines = [f"\n[events.{_key(name)}]"]
        lines.append(f"description = {_str(str(e.get('description', '')))}")
        for k, v in e.items():
            if k == "description":
                continue
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                lines.append(f"{_key(k)} = [{', '.join(_str(x) for x in v)}]")
            elif isinstance(v, str):
                lines.append(f"{_key(k)} = {_str(v)}")
            elif isinstance(v, bool):
                lines.append(f"{_key(k)} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{_key(k)} = {v}")
            # other hand-written values (tables, dates) aren't kept; rare in a catalog
        parts.append("\n".join(lines) + "\n")
    return "".join(parts)


def write(path: Path, seen: dict[str, dict]) -> tuple[int, int]:
    """Merge the run's events into the catalog file: (added, total)."""
    existing = load(path) if path.is_file() else {}
    merged = merge(existing, seen)
    path.write_text(dumps(merged), encoding="utf-8")
    return len(set(merged) - set(existing)), len(merged)


def text(seen: dict[str, dict]) -> str:
    if not seen:
        return "no events (no JSON lines with a string 'event')"
    out = [f"{len(seen)} events"]
    for name, e in seen.items():
        out.append(f"  {e['count']}\t{name}\t{','.join(e['levels']) or '-'}\t"
                   f"line {e['first_line']}\tfields: {', '.join(e['fields']) or '-'}")
    return "\n".join(out)


def _md(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace("|", "\\|").replace("`", "'").replace("\n", " "))


def markdown(seen: dict[str, dict], described: dict[str, dict] | None = None) -> str:
    described = described or {}
    out = ["| Event | Count | Levels | Fields | Description |", "|---|---|---|---|---|"]
    for name, e in seen.items():
        desc = described.get(name, {}).get("description", "")
        out.append(f"| `{_md(name)}` | {e['count']} | {', '.join(e['levels'])} | "
                   f"{', '.join(f'`{_md(f)}`' for f in e['fields'])} | {_md(str(desc))} |")
    return "\n".join(out) + "\n"
