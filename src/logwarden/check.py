"""Check JSON-lines logs: convert to Weaver samples, run live-check, summarise.

The result dict is a public interface (`--json`, MCP, CI): only add keys.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from logwarden import __version__, data, weaver

MAX_FINDINGS = 500
_SEVERITY = {"debug": 5, "info": 9, "warning": 13, "warn": 13, "error": 17, "critical": 21, "fatal": 21}
_RECORD_KEYS = {"timestamp", "level", "event", "trace_id", "span_id"}
_NAMING_RULES = {"adr_banned_synonym", "adr_semconv_prefix"}
NON_JSON = "adr_non_json_line"
BASELINE_FORMAT = 1


class CheckError(RuntimeError):
    """Setup problem (bad fields file, weaver failure). CLI exit code 2."""


@dataclass
class Record:
    line: int
    obj: dict


@dataclass
class BadLine:
    line: int
    kind: str      # "plain text", "JSON array", ...
    text: str      # only hashed into the baseline fingerprint, never printed


def parse(text: str) -> tuple[list[Record], list[BadLine], int]:
    """JSON objects and non-blank non-object lines, with 1-based line numbers, and the total."""
    # Split on "\n" only, like `jq -R` (str.splitlines also splits on \x0b, \u2028, ...).
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    records, bad = [], []
    for n, raw in enumerate(lines, start=1):
        try:
            obj = json.loads(raw)
        except ValueError:
            if raw.strip():
                bad.append(BadLine(n, "plain text", raw))
            continue
        if isinstance(obj, dict):
            records.append(Record(n, obj))
        else:
            bad.append(BadLine(n, _json_kind(obj), raw))
    return records, bad, len(lines)


def _json_kind(v: Any) -> str:
    if isinstance(v, dict):
        return "JSON object"
    if isinstance(v, list):
        return "JSON array"
    if isinstance(v, str):
        return "JSON string"
    if isinstance(v, bool) or v is None:
        return "JSON literal"
    return "JSON number"


def parse_lines(text: str) -> tuple[list[Record], int]:
    """JSON objects with their 1-based line numbers, and the total number of lines."""
    records, _, total = parse(text)
    return records, total


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


def _text(v: Any) -> Any:
    """Weaver requires strings for body/trace_id/span_id; one non-string would fail the whole run."""
    if _falsy(v) or isinstance(v, str):
        return v
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def to_sample(obj: dict) -> dict:
    """One structlog-style JSON record -> Weaver live-check log sample."""
    level = obj.get("level")
    level_s = "" if _falsy(level) else str(level)
    event = _text(obj.get("event"))
    return {"log": {
        # event_name stays empty: events are not declared in the registry, so Weaver
        # would report each one as missing_event. The event goes in body.
        "event_name": "",
        "body": "" if _falsy(event) else event,
        "severity_number": _SEVERITY.get((level_s or "info").lower(), 9),
        "severity_text": level_s.upper(),
        "trace_id": None if _falsy(obj.get("trace_id")) else _text(obj["trace_id"]),
        "span_id": None if _falsy(obj.get("span_id")) else _text(obj["span_id"]),
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


@dataclass
class Policy:
    """What the project configured: rules switched off (with reasons) and a baseline."""
    disabled: dict[str, str] = field(default_factory=dict)
    baseline: dict | None = None
    baseline_path: str | None = None


_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEXID = re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b")
_DIGITS = re.compile(r"\d+")


def mask(s: str) -> str:
    """Hide what changes between runs (ids, numbers) so a finding matches its baseline entry."""
    return _DIGITS.sub("#", _HEXID.sub("#", _UUID.sub("#", s)))[:120]


def _str(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def fingerprint(f: dict) -> tuple[str, str, str, str]:
    return (f["rule"], f["field"] or "", f["logger"], f["fp_event"])


def a_id(a: dict) -> str | None:
    return a.get("id") or a.get("advice_type")


def collect(live_check: dict, records: list[Record], bad: list[BadLine]) -> list[dict]:
    """Every finding: Weaver's (per sample) plus logwarden's own line rules."""
    found = []
    for i, sample in enumerate(live_check.get("samples") or []):
        log = sample.get("log") or {}
        rec = records[i]
        logger = _str(rec.obj.get("logger"))
        for node in _walk(log):
            if isinstance(node, dict) and "all_advice" in node:
                for a in node["all_advice"] or []:
                    # Weaver 0.28 emits {id, level}; older builds used advice_type/advice_level.
                    found.append({
                        "rule": a_id(a),
                        "level": a.get("level") or a.get("advice_level"),
                        "field": (a.get("context") or {}).get("attribute_key"),
                        # The field itself must go, so "unknown field" would be noise.
                        "renames": a_id(a) in _NAMING_RULES or (a.get("context") or {}).get("match") == "key",
                        "message": a.get("message"),
                        "line": rec.line,
                        "event": log.get("body"),
                        "logger": logger,
                        "fp_event": mask(_str(log.get("body"))),
                    })
    # A non-string event is sent to Weaver as JSON text, where `true` would pass as a name.
    odd = {r.line: r.obj["event"] for r in records
           if not _falsy(r.obj.get("event")) and not isinstance(r.obj["event"], str)}
    if odd:
        found = [f for f in found if not (f["rule"] == "adr_event_name" and f["line"] in odd)]
        by_line = {r.line: r for r in records}
        for line, ev in odd.items():
            body = _str(ev)
            found.append({"rule": "adr_event_name", "level": "violation", "field": None,
                          "message": "§4: event must be a constant snake_case string, not a JSON "
                                     + ("boolean" if isinstance(ev, bool) else _json_kind(ev)[5:].lower()),
                          "line": line, "event": body, "logger": _str(by_line[line].obj.get("logger")),
                          "fp_event": mask(body), "renames": False})
    for b in bad:
        digest = hashlib.sha1(mask(b.text.strip()).encode("utf-8")).hexdigest()[:12]
        found.append({"rule": NON_JSON, "level": "violation", "field": None,
                      "message": f"§1: line is not a JSON object ({b.kind})",
                      "line": b.line, "event": None, "logger": "", "fp_event": f"line:{digest}"})
    found.sort(key=lambda f: f["line"])
    return found


def violations_of(found: list[dict]) -> list[dict]:
    # A field already flagged by a naming rule (or as a secret key) is not also reported as unknown.
    named = {(f["line"], f["field"]) for f in found if f.get("renames")}
    return [f for f in found if f["level"] == "violation"
            and not (f["rule"] == "missing_attribute" and (f["line"], f["field"]) in named)]


def summarise(found: list[dict], n_records: int, total_lines: int, policy: Policy | None = None) -> dict:
    """All findings -> logwarden result, after `disable` and the baseline."""
    policy = policy or Policy()
    rules = data.rules()
    off = {r: 0 for r in policy.disabled}
    kept = []
    for f in found:
        if f["rule"] in off:
            off[f["rule"]] += f["level"] == "violation"
        else:
            kept.append(f)
    violations = violations_of(kept)

    baselined = fixed = 0
    if policy.baseline is not None:
        known = {(e["rule"], e["field"], e["logger"], e["event"]) for e in policy.baseline["entries"]
                 if e["rule"] not in off}
        seen = {fingerprint(v) for v in violations}
        new = [v for v in violations if fingerprint(v) not in known]
        baselined = len(violations) - len(new)
        fixed = len(known - seen)
        violations = new

    by_rule = []
    for rule in sorted({v["rule"] for v in violations}, key=str):
        group = [v for v in violations if v["rule"] == rule]
        meta = rules.get(rule, {})
        by_rule.append({"rule": rule, "count": len(group), "title": meta.get("title"),
                        "hint": meta.get("hint"), "doc": meta.get("doc"),
                        "example": group[0]["message"]})
    by_rule.sort(key=lambda r: -r["count"])

    others = [f for f in kept if f["level"] != "violation"]
    advice = []
    for rule in sorted({f["rule"] for f in others}, key=str):
        group = [f for f in others if f["rule"] == rule]
        advice.append({"rule": rule, "level": group[0]["level"], "count": len(group)})

    return {
        "tool": "logwarden",
        "version": __version__,
        "ok": not violations,
        "records": n_records,
        "skipped_lines": total_lines - n_records,
        "violations": len(violations),
        "by_rule": by_rule,
        "findings": [{k: f[k] for k in ("line", "event", "rule", "field", "message")}
                     for f in violations[:MAX_FINDINGS]],
        "findings_truncated": len(violations) > MAX_FINDINGS,
        "advice": advice,
        "baseline": policy.baseline_path,
        "baselined": baselined,
        "baseline_fixed": fixed,
        "disabled": [{"rule": r, "reason": policy.disabled[r], "count": off[r]} for r in sorted(off)],
    }


def make_baseline(found: list[dict], policy: Policy | None = None) -> dict:
    """Baseline file content: the current violations (minus disabled rules), by fingerprint."""
    disabled = (policy or Policy()).disabled
    counts: dict[tuple, int] = {}
    for v in violations_of([f for f in found if f["rule"] not in disabled]):
        counts[fingerprint(v)] = counts.get(fingerprint(v), 0) + 1
    return {
        "tool": "logwarden",
        "format": BASELINE_FORMAT,
        "version": __version__,
        "about": "Known findings that `logwarden check` doesn't fail on. Regenerate with "
                 "`logwarden baseline`; new findings still fail.",
        "entries": [{"rule": k[0], "field": k[1], "logger": k[2], "event": k[3], "count": n}
                    for k, n in sorted(counts.items())],
    }


def load_baseline(path: Path) -> dict:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CheckError(f"can't read baseline {path}: {e}") from e
    if not isinstance(content, dict) or content.get("format") != BASELINE_FORMAT \
            or not isinstance(content.get("entries"), list):
        raise CheckError(f"{path} is not a logwarden baseline (format {BASELINE_FORMAT}); "
                         "regenerate it with 'logwarden baseline'")
    return content


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


def evaluate(text: str, *, fields: Path | None = None, registry: Path | None = None,
             out: Path | None = None) -> tuple[list[dict], int, int]:
    """Run Weaver on log text: (all findings, JSON records, total lines). Raises CheckError."""
    if fields and registry:
        raise CheckError("use fields or registry, not both")
    if fields and not fields.is_file():
        raise CheckError(f"no such fields file: {fields}")
    try:
        binary = weaver.find()
    except weaver.WeaverError as e:
        raise CheckError(str(e)) from e

    records, bad, total = parse(text)
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
        found = collect(json.loads(report.read_text(encoding="utf-8")), records, bad)
    return found, len(records), total


def run(text: str, *, fields: Path | None = None, registry: Path | None = None,
        out: Path | None = None, policy: Policy | None = None) -> dict:
    """Check log text. Raises CheckError on setup problems."""
    found, n_records, total = evaluate(text, fields=fields, registry=registry, out=out)
    result = summarise(found, n_records, total, policy)
    if out:
        (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                         encoding="utf-8")
    return result
