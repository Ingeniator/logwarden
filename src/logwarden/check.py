"""Check JSON-lines logs: convert to Weaver samples, run live-check, summarise.

The result dict is a public interface (`--json`, MCP, CI): only add keys.
"""
from __future__ import annotations

import json
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from logwarden import __version__, data, weaver

MAX_FINDINGS = 500
_SEVERITY = {"debug": 5, "info": 9, "warning": 13, "warn": 13, "error": 17, "critical": 21, "fatal": 21}
_RECORD_KEYS = {"timestamp", "level", "event", "trace_id", "span_id"}
_NAMING_RULES = {"adr_banned_synonym", "adr_semconv_prefix"}


class CheckError(RuntimeError):
    """Setup problem (bad fields file, weaver failure). CLI exit code 2."""


@dataclass
class Record:
    line: int
    obj: dict


def parse_lines(text: str) -> tuple[list[Record], int]:
    """JSON objects with their 1-based line numbers, and the total number of lines."""
    # Split on "\n" only, like `jq -R` (str.splitlines also splits on \x0b,  , ...).
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    records = []
    for n, raw in enumerate(lines, start=1):
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict):
            records.append(Record(n, obj))
    return records, len(lines)


def _weaver_type(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return "double"
        return "int" if math.floor(v) == v else "double"
    return "string"


def _weaver_value(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"), ensure_ascii=False)
    if v is None:
        return ""
    return v


def _falsy(v: Any) -> bool:  # jq's `//` treats null and false as missing
    return v is None or v is False


def to_sample(obj: dict) -> dict:
    """One structlog-style JSON record -> Weaver live-check log sample."""
    level = obj.get("level")
    level_s = "" if _falsy(level) else str(level)
    event = obj.get("event")
    return {"log": {
        # event_name stays empty: events are not declared in the registry, so Weaver
        # would report each one as missing_event. The event goes in body.
        "event_name": "",
        "body": "" if _falsy(event) else event,
        "severity_number": _SEVERITY.get((level_s or "info").lower(), 9),
        "severity_text": level_s.upper(),
        "trace_id": None if _falsy(obj.get("trace_id")) else obj["trace_id"],
        "span_id": None if _falsy(obj.get("span_id")) else obj["span_id"],
        "attributes": [
            {"name": k, "type": _weaver_type(v), "value": _weaver_value(v)}
            for k, v in obj.items() if k not in _RECORD_KEYS
        ],
    }}


def _walk(node: Any) -> Iterable[Any]:
    """jq's `..`: the node, then its children depth-first, in key order."""
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def summarise(live_check: dict, lines: list[int], total_lines: int) -> dict:
    """Weaver live_check.json -> logwarden result."""
    rules = data.rules()
    samples = live_check.get("samples") or []
    found = []
    for i, sample in enumerate(samples):
        log = sample.get("log") or {}
        for node in _walk(log):
            if isinstance(node, dict) and "all_advice" in node:
                for a in node["all_advice"] or []:
                    # Weaver 0.28 emits {id, level}; older builds used advice_type/advice_level.
                    found.append({
                        "rule": a.get("id") or a.get("advice_type"),
                        "level": a.get("level") or a.get("advice_level"),
                        "field": (a.get("context") or {}).get("attribute_key"),
                        "message": a.get("message"),
                        "line": lines[i],
                        "event": log.get("body"),
                    })

    # A field already flagged by a naming rule is not also reported as unknown.
    named = {(f["line"], f["field"]) for f in found if f["rule"] in _NAMING_RULES}
    violations = [f for f in found if f["level"] == "violation"
                  and not (f["rule"] == "missing_attribute" and (f["line"], f["field"]) in named)]

    by_rule = []
    for rule in sorted({v["rule"] for v in violations}, key=str):
        group = [v for v in violations if v["rule"] == rule]
        meta = rules.get(rule, {})
        by_rule.append({"rule": rule, "count": len(group), "title": meta.get("title"),
                        "hint": meta.get("hint"), "doc": meta.get("doc"),
                        "example": group[0]["message"]})
    by_rule.sort(key=lambda r: -r["count"])

    others = [f for f in found if f["level"] != "violation"]
    advice = []
    for rule in sorted({f["rule"] for f in others}, key=str):
        group = [f for f in others if f["rule"] == rule]
        advice.append({"rule": rule, "level": group[0]["level"], "count": len(group)})

    ordered = sorted(violations, key=lambda f: f["line"])
    return {
        "tool": "logwarden",
        "version": __version__,
        "ok": not violations,
        "records": len(samples),
        "skipped_lines": total_lines - len(samples),
        "violations": len(violations),
        "by_rule": by_rule,
        "findings": [{k: f[k] for k in ("line", "event", "rule", "field", "message")}
                     for f in ordered[:MAX_FINDINGS]],
        "findings_truncated": len(violations) > MAX_FINDINGS,
        "advice": advice,
    }


def _fields_registry(fields: Path, into: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    (into / "fields.yaml").write_bytes(fields.read_bytes())
    (into / "manifest.yaml").write_text(
        "name: project\n"
        f"description: Project-only log fields from {fields.name}\n"
        # Must differ from logwarden's own schema_url, or Weaver sees a circular dependency.
        "schema_url: https://logwarden.invalid/project/0.0.0\n"
        "dependencies:\n"
        "  - schema_url: https://github.com/Ingeniator/logwarden/schemas/0.1.0\n"
        f"    registry_path: {data.path('registry').as_posix()}\n",
        encoding="utf-8",
    )
    return into


def run(text: str, *, fields: Path | None = None, registry: Path | None = None,
        out: Path | None = None) -> dict:
    """Check log text. Raises CheckError on setup problems."""
    if fields and registry:
        raise CheckError("use fields or registry, not both")
    if fields and not fields.is_file():
        raise CheckError(f"no such fields file: {fields}")
    try:
        binary = weaver.find()
    except weaver.WeaverError as e:
        raise CheckError(str(e)) from e

    records, total = parse_lines(text)
    with tempfile.TemporaryDirectory(prefix="logwarden-") as tmp:
        work = out or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        if fields:
            registry = _fields_registry(fields, Path(tmp) / "registry")
        samples = work / "samples.json"
        samples.write_text(json.dumps([to_sample(r.obj) for r in records], ensure_ascii=False),
                           encoding="utf-8")
        proc = subprocess.run(
            [binary, "registry", "live-check",
             "--registry", str(registry or data.path("registry")),
             "--include-unreferenced",
             "--config", str(data.path("weaver.toml")),
             "--advice-policies", str(data.path("policies")),
             "--input-source", str(samples),
             "--input-format", "json",
             "--format", "json",
             "--output", str(work),
             "--quiet"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        (work / "weaver.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        report = work / "live_check.json"
        if not report.is_file() or report.stat().st_size == 0:
            log = proc.stdout + proc.stderr
            errors = [ln for ln in log.splitlines() if "×" in ln or "rror" in ln]
            raise CheckError("weaver produced no report (registry or fields file invalid?)\n"
                             + "\n".join(errors or log.splitlines()[-20:]))
        result = summarise(json.loads(report.read_text(encoding="utf-8")),
                           [r.line for r in records], total)
        if out:
            (work / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
        return result
