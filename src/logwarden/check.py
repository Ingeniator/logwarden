"""Check JSON-lines logs: convert to Weaver samples, run live-check, summarise.

The result dict is a public interface (`--json`, MCP, CI): only add keys.
"""
from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from logwarden import __version__, data, sources, weaver

MAX_FINDINGS = 500
_SEVERITY = {"debug": 5, "info": 9, "warning": 13, "warn": 13, "error": 17, "critical": 21, "fatal": 21}
_RECORD_KEYS = {"timestamp", "level", "event", "trace_id", "span_id"}
_NAMING_RULES = {"adr_banned_synonym", "adr_semconv_prefix"}
NON_JSON = "adr_non_json_line"
# Rule profiles: `essential` is what any service should pass as it is; `standard` is every rule.
PROFILES: dict[str, frozenset[str] | None] = {
    "essential": frozenset({"adr_sensitive_data", NON_JSON, "adr_missing_required"}),
    "standard": None,
}
FIX_FIRST = 10
UPSTREAM = "adr_upstream_failure_coverage"
EVENT_CATALOG = "adr_event_catalog"
SENSITIVE = "adr_sensitive_data"
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
BASELINE_FORMAT = 1


class CheckError(RuntimeError):
    """Setup problem (bad fields file, weaver failure). CLI exit code 2."""


@dataclass
class Record:
    line: int                  # line, CSV row or search hit number
    obj: dict
    id: str | None = None      # Elasticsearch _id, when the export has it
    service: str = sources.NO_SERVICE
    file_line: int | None = None


@dataclass
class BadLine:
    line: int
    kind: str      # "plain text", "JSON array", ...
    text: str      # only hashed into the baseline fingerprint, never printed
    id: str | None = None
    service: str = sources.NO_SERVICE
    file_line: int | None = None


@dataclass
class Input:
    records: list[Record]
    bad: list[BadLine]
    total: int
    format: str = "jsonl"
    missing_columns: list[str] = field(default_factory=list)
    matched: int | None = None


def read(text: str, src: sources.Source | None = None, types=dict) -> Input:
    """Records and non-object lines of `text` in any supported format. Raises CheckError."""
    src = src or sources.Source()
    try:
        loaded = sources.load(text, src, types)
    except sources.SourceError as e:
        raise CheckError(str(e)) from e
    records, bad = [], []
    for i in loaded.items:
        svc = sources.service_of(i, src)
        if i.obj is not None:
            records.append(Record(i.n, i.obj, i.id, svc, i.file_line))
        else:
            bad.append(BadLine(i.n, i.kind, i.text or "", i.id, svc, i.file_line))
    return Input(records, bad, loaded.total, loaded.format, loaded.missing_columns, loaded.matched)


def parse(text: str) -> tuple[list[Record], list[BadLine], int]:
    """JSON objects and non-blank non-object lines, with 1-based line numbers, and the total."""
    inp = read(text, sources.Source(format="jsonl"))
    return inp.records, inp.bad, inp.total


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


def _scalar_type(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return "double"
        return "int" if math.floor(v) == v else "double"
    return "string"


def _array_type(v: list) -> str | None:
    """string[], int[], double[] or boolean[] for a list whose items are all one type
    (ints and doubles together are double[]); None for an empty or mixed list, or one with
    nulls, lists or objects in it."""
    if not v or any(x is None or isinstance(x, (dict, list)) for x in v):
        return None
    kinds = {_scalar_type(x) for x in v}
    if kinds == {"int", "double"}:
        return "double[]"
    return f"{kinds.pop()}[]" if len(kinds) == 1 else None


def _weaver_type(v: Any) -> str:
    if isinstance(v, list):
        return _array_type(v) or "string"
    return _scalar_type(v)


def _weaver_value(v: Any) -> Any:
    if isinstance(v, list) and _array_type(v):
        return v
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


def _attribute(name: str, v: Any, declared: dict[str, str]) -> dict:
    """An empty list has no item type to go by: it gets the field's declared array type, if any."""
    if v == [] and declared.get(name, "").endswith("[]"):
        return {"name": name, "type": declared[name], "value": []}
    return {"name": name, "type": _weaver_type(v), "value": _weaver_value(v)}


def to_sample(obj: dict, declared: dict[str, str] | None = None) -> dict:
    """One structlog-style JSON record -> Weaver live-check log sample. `declared`: field name ->
    registry type, used only for empty lists."""
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
        "attributes": [_attribute(k, v, declared or {}) for k, v in obj.items() if k not in _RECORD_KEYS],
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
    upstreams: list[str] = field(default_factory=list)
    events: dict | None = None          # the event catalog's events, when configured
    events_path: str | None = None
    source: sources.Source = field(default_factory=sources.Source)
    profile: str = "standard"

    def outside(self, rule: str) -> bool:
        """The rule isn't part of the chosen profile."""
        rules = PROFILES.get(self.profile)
        return rules is not None and rule not in rules

    def off(self, rule: str) -> bool:
        """Not checked: disabled, or outside the profile."""
        return rule in self.disabled or self.outside(rule)


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


def fingerprint(f: dict) -> tuple[str, str, str, str, str]:
    return (f["rule"], f["field"] or "", f["logger"], f["fp_event"], f.get("kind") or "")


def _entry_key(e: dict) -> tuple[str, str, str, str, str]:
    return (e["rule"], e["field"], e["logger"], e["event"], e.get("kind") or "")


def a_id(a: dict) -> str | None:
    return a.get("id") or a.get("advice_type")


def _is_failure(obj: dict) -> bool:
    level = str(obj.get("level") or "").lower()
    event = obj.get("event")
    return _SEVERITY.get(level, 9) >= 13 or (isinstance(event, str) and event.endswith("_failed"))


def upstream_findings(records: list[Record], upstreams: list[str]) -> list[dict]:
    """§10: every listed upstream has a failure line in the run (whole-run rule, line None)."""
    covered = {r.obj.get("server.address") for r in records if _is_failure(r.obj)}
    return [{"rule": UPSTREAM, "level": "violation", "field": "server.address",
             "message": f"§10: no failure log for upstream '{host}' in this run; add a test where "
                        "a call to it fails and check the failure line",
             "line": None, "event": None, "logger": "", "fp_event": host, "renames": False}
            for host in upstreams if host not in covered]


def catalog_findings(records: list[Record], events: dict | None, path: str | None) -> list[dict]:
    """§4: with a catalog configured, every (well-formed) event name is listed in it."""
    if events is None:
        return []
    where = path or "the event catalog"
    return [{"rule": EVENT_CATALOG, "level": "violation", "field": None,
             "message": f"§4: event '{ev}' is not in {where}; add it with a description "
                        "(logwarden catalog LOGS -o FILE adds new events)",
             "line": r.line, "event": ev, "logger": _str(r.obj.get("logger")), "fp_event": ev,
             "renames": False}
            for r in records
            # A malformed name is adr_event_name's finding; don't report it twice.
            if isinstance(ev := r.obj.get("event"), str) and _SNAKE.match(ev) and ev not in events]


def collect(live_check: dict, records: list[Record], bad: list[BadLine],
            upstreams: list[str] | None = None, events: dict | None = None,
            events_path: str | None = None, missing_columns: list[str] | None = None) -> list[dict]:
    """Every finding: Weaver's (per sample) plus logwarden's own line and run rules."""
    found = _collect(live_check, records, bad, upstreams, events, events_path)
    absent = set(missing_columns or [])
    by_line = {r.line: r for r in records}
    by_line_bad = {b.line: b for b in bad}
    for f in found:
        src = by_line.get(f["line"]) or by_line_bad.get(f["line"])
        f["service"] = src.service if src else None
        f["id"] = src.id if src else None
        f["file_line"] = src.file_line if src else None
        f["loc"] = code_location(src.obj) if isinstance(src, Record) else None
    # A field the export left out may well be in the log; don't report it as missing.
    return [f for f in found if not (f["rule"] == "adr_missing_required" and f["field"] in absent)]


def code_location(obj: dict) -> tuple[str, int | None, str | None] | None:
    """Where the line was logged: code.file.path / code.line.number (or the older names)."""
    path = obj.get("code.file.path", obj.get("code.filepath"))
    if not isinstance(path, str) or not path:
        return None
    line = obj.get("code.line.number", obj.get("code.lineno"))
    func = obj.get("code.function.name", obj.get("code.function"))
    return (path, line if isinstance(line, int) and not isinstance(line, bool) and line > 0 else None,
            func if isinstance(func, str) else None)


def _collect(live_check: dict, records: list[Record], bad: list[BadLine],
             upstreams: list[str] | None, events: dict | None, events_path: str | None) -> list[dict]:
    found = []
    for i, sample in enumerate(live_check.get("samples") or []):
        log = sample.get("log") or {}
        rec = records[i]
        logger = _str(rec.obj.get("logger"))
        for node in (log["advice"] if "advice" in log else _walk(log)):   # live_check() gives "advice"
            if isinstance(node, dict) and "all_advice" in node:
                for a in node["all_advice"] or []:
                    ctx = a.get("context") or {}
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
                        # What a secret finding detected (key, nested, jwt, card_number, ...): one
                        # kind on a field in the baseline mustn't hide another kind there.
                        "kind": ctx.get("match") if a_id(a) == SENSITIVE else None,
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
    found += catalog_findings(records, events, events_path)
    found.sort(key=lambda f: f["line"])
    return found + upstream_findings(records, upstreams or [])


# Rules that mean "rename this field or stop logging it", so init --from doesn't declare it.
NOT_DECLARED = {"adr_banned_synonym": "rename it to the semconv name",
                "adr_semconv_prefix": "use the dotted semconv name",
                "adr_sensitive_data": "don't log it"}


def declarable(violations: list[dict]) -> set[str]:
    """Unknown fields that no rule asks to rename or drop: what init --from declares."""
    flagged = {v["field"] for v in violations if v["rule"] in NOT_DECLARED and v.get("field")}
    return {v["field"] for v in violations if v["rule"] == "missing_attribute" and v.get("field")} - flagged


def violations_of(found: list[dict]) -> list[dict]:
    # A field already flagged by a naming rule (or as a secret key) is not also reported as unknown.
    named = {(f["line"], f["field"]) for f in found if f.get("renames")}
    return [f for f in found if f["level"] == "violation"
            and not (f["rule"] == "missing_attribute" and (f["line"], f["field"]) in named)]


@dataclass
class Outcome:
    """Findings after `disable` and the baseline."""
    kept: list[dict]            # not disabled (violations and advice)
    violations: list[dict]      # what fails the run
    off: dict[str, int]         # disabled rule -> violations not counted
    baselined: int = 0
    fixed: int = 0
    outside: int = 0            # violations of rules outside the profile, not counted
    outside_unknown: int = 0    # of those: unknown fields (missing_attribute), which init --from declares
    outdated: int = 0           # baseline entries for secrets without a kind (before v0.10.2): never match


def apply_policy(found: list[dict], policy: Policy | None = None) -> Outcome:
    policy = policy or Policy()
    off = {r: 0 for r in policy.disabled}
    enabled = []
    for f in found:
        if f["rule"] in off:
            off[f["rule"]] += f["level"] == "violation"
        else:
            enabled.append(f)
    kept = [f for f in enabled if not policy.outside(f["rule"])]
    violations = violations_of(kept)
    all_enabled = violations_of(enabled)
    outside = len(all_enabled) - len(violations)
    names = declarable(all_enabled)
    outside_unknown = sum(1 for v in all_enabled if v["rule"] == "missing_attribute"
                          and policy.outside(v["rule"]) and v["field"] in names)

    baselined = fixed = outdated = 0
    if policy.baseline is not None:
        entries = [e for e in policy.baseline["entries"] if not policy.off(e["rule"])]
        # A secret entry from before v0.10.2 covers a field whatever was found in it, so it
        # would hide a new secret of another kind there: it matches nothing until regenerated.
        old = [e for e in entries if e["rule"] == SENSITIVE and not e.get("kind")]
        outdated = len(old)
        known = {_entry_key(e) for e in entries if e not in old}
        seen = {fingerprint(v) for v in violations}
        new = [v for v in violations if fingerprint(v) not in known]
        baselined = len(violations) - len(new)
        fixed = len(known - seen)
        violations = new
    return Outcome(kept, violations, off, baselined, fixed, outside, outside_unknown, outdated)


def fix_first(violations: list[dict], limit: int = FIX_FIRST) -> list[dict]:
    """Violations grouped by what causes them, the biggest first.

    A group is one source line (code.file.path + code.line.number), else one logger and event;
    non-JSON lines form one group. Every log line belongs to exactly one group, so the shares
    of all groups add up to 100% of the lines with violations.
    """
    groups: dict[tuple, dict] = {}
    for v in violations:
        if v["line"] is None:           # run-level findings (upstream coverage) have no line
            continue
        loc = v.get("loc")
        if v["rule"] == NON_JSON:
            key = ("non_json",)
        elif loc and loc[1]:
            key = ("code", loc[0], loc[1])
        else:
            key = ("logger", v["logger"], v["fp_event"])
        g = groups.setdefault(key, {"lines": set(), "rules": {}, "fields": set(),
                                    "logger": v["logger"] or None, "event": v["fp_event"]})
        g["lines"].add(v["line"])
        g["rules"][v["rule"]] = g["rules"].get(v["rule"], 0) + 1
        if v["field"]:
            g["fields"].add(v["field"])
    total = len({ln for g in groups.values() for ln in g["lines"]})
    out = []
    for key, g in sorted(groups.items(), key=lambda kv: (-len(kv[1]["lines"]), str(kv[0]))):
        kind = key[0]
        out.append({
            "kind": kind,
            "where": f"{key[1]}:{key[2]}" if kind == "code" else None,
            "logger": None if kind == "non_json" else g["logger"],
            "event": None if kind == "non_json" else g["event"],
            "lines": len(g["lines"]),
            "share": round(100.0 * len(g["lines"]) / total, 1) if total else 0.0,
            "rules": dict(sorted(g["rules"].items(), key=lambda kv: (-kv[1], kv[0]))),
            "fields": sorted(g["fields"])[:10],
        })
    return out[:limit]


def by_service(violations: list[dict], lines: dict[str, int]) -> list[dict]:
    """Per service: lines, violations by rule, lines with a violation and the compliance %."""
    out = []
    for svc in sorted(lines):
        vs = [v for v in violations if v.get("service") == svc and v["line"] is not None]
        rules: dict[str, int] = {}
        for v in vs:
            rules[v["rule"]] = rules.get(v["rule"], 0) + 1
        bad_lines = len({v["line"] for v in vs})
        n = lines[svc]
        out.append({
            "service": svc, "lines": n, "violations": len(vs), "lines_with_violations": bad_lines,
            "compliance": round(100.0 * (n - bad_lines) / n, 1) if n else 100.0,
            "by_rule": dict(sorted(rules.items(), key=lambda kv: (-kv[1], kv[0]))),
            "top_rule": min(rules, key=lambda r: (-rules[r], r)) if rules else None,
        })
    out.sort(key=lambda s: (-s["violations"], s["service"]))
    return out


def _finding(f: dict) -> dict:
    out = {k: f[k] for k in ("line", "event", "rule", "field", "message")}
    out["service"] = f.get("service")
    if f.get("id"):
        out["id"] = f["id"]
    return out


def summarise(found: list[dict], n_records: int, total_lines: int, policy: Policy | None = None,
              lines: dict[str, int] | None = None, inp: Input | None = None) -> dict:
    """All findings -> logwarden result, after `disable` and the baseline."""
    policy = policy or Policy()
    rules = data.rules()
    o = apply_policy(found, policy)
    kept, violations, off, baselined, fixed = o.kept, o.violations, o.off, o.baselined, o.fixed

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
        "findings": [_finding(f) for f in violations[:MAX_FINDINGS]],
        "findings_truncated": len(violations) > MAX_FINDINGS,
        "advice": advice,
        "baseline": policy.baseline_path,
        "baselined": baselined,
        "baseline_fixed": fixed,
        "baseline_outdated": o.outdated,
        "disabled": [{"rule": r, "reason": policy.disabled[r], "count": off[r]} for r in sorted(off)],
        "services": by_service(violations, lines or {}),
        "profile": policy.profile,
        "outside_profile": o.outside,
        "outside_profile_unknown_fields": o.outside_unknown,
        "lines_with_violations": len({v["line"] for v in violations if v["line"] is not None}),
        "fix_first": fix_first(violations),
        "input": {"format": inp.format if inp else "jsonl",
                  "missing_columns": inp.missing_columns if inp else [],
                  "matched": inp.matched if inp else None},
    }


def make_baseline(found: list[dict], policy: Policy | None = None) -> dict:
    """Baseline file content: the current violations (minus disabled rules), by fingerprint."""
    policy = policy or Policy()
    counts: dict[tuple, int] = {}
    for v in violations_of([f for f in found if not policy.off(f["rule"])]):
        counts[fingerprint(v)] = counts.get(fingerprint(v), 0) + 1
    return {
        "tool": "logwarden",
        "format": BASELINE_FORMAT,
        "version": __version__,
        "about": "Known findings that `logwarden check` doesn't fail on. Regenerate with "
                 "`logwarden baseline`; new findings still fail.",
        "entries": [{"rule": k[0], "field": k[1], "logger": k[2], "event": k[3],
                     **({"kind": k[4]} if k[4] else {}), "count": n}
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


# Bump when _build_chain changes what it writes, so cached chains from older versions aren't used.
CHAIN_FORMAT = 1


def base_registry(into: Path) -> Path:
    """logwarden's registry (copied into `into`), depending on the GenAI semantic conventions,
    which depend on the semantic conventions without the directories GenAI replaces.

    Semconv keeps deprecated copies of the fields that moved to GenAI (in gen-ai/, mcp/,
    openai/). With both loaded, Weaver sees each such field twice and uses whichever group comes
    last, so current GenAI fields could be reported as deprecated."""
    src = data.path("registry")
    manifest = src / "manifest.yaml"
    genai = base_chain(into)
    own = into / "logwarden"
    own.mkdir(parents=True)
    for f in src.iterdir():
        if f.is_file() and f.name != "manifest.yaml":
            (own / f.name).write_bytes(f.read_bytes())
    _point_at(own / "manifest.yaml", manifest.read_text(encoding="utf-8"), genai)
    return own


def base_chain(scratch: Path) -> Path:
    """The GenAI registry directory, its manifest pointing at semconv without the replaced
    directories. Built once in the cache for the pinned versions; built under `scratch` when
    LOGWARDEN_SEMCONV(_GENAI) points at a local copy (its content can change) or the cache
    can't be written."""
    manifest = data.path("registry") / "manifest.yaml"
    sv, gv = weaver.semconv_version(manifest), weaver.genai_version(manifest)
    semconv, genai = weaver.semconv_model(sv), weaver.genai_model(gv)
    if os.environ.get("LOGWARDEN_SEMCONV") or os.environ.get("LOGWARDEN_SEMCONV_GENAI"):
        return _build_chain(semconv, genai, scratch / "chain", scratch / "chain")
    dest = weaver.cache_dir() / f"registry-semconv-{sv}-genai-{gv}-{CHAIN_FORMAT}"
    if _chain_ok(dest):
        return dest / "genai"
    # A name of its own per process, so parallel first runs don't build into the same place.
    tmp = dest.with_name(f"{dest.name}.tmp-{os.getpid()}")
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        _build_chain(semconv, genai, tmp, dest)
        if dest.exists():                    # a copied cache: its manifest points elsewhere
            shutil.rmtree(dest)
        try:
            tmp.rename(dest)
        except OSError:                      # another run renamed its copy first
            shutil.rmtree(tmp, ignore_errors=True)
            if not _chain_ok(dest):
                raise
    except OSError:                          # read-only or full cache: build it for this run
        shutil.rmtree(tmp, ignore_errors=True)
        return _build_chain(semconv, genai, scratch / "chain", scratch / "chain")
    return dest / "genai"


def _chain_ok(dest: Path) -> bool:
    """A complete cached chain whose GenAI manifest points at its own semconv copy (Weaver
    resolves a relative registry_path from the working directory, so the path is absolute and
    a cache copied to another directory has to be rebuilt)."""
    try:
        text = (dest / "genai" / "manifest.yaml").read_text(encoding="utf-8")
    except OSError:
        return False
    m = re.search(r"^\s*registry_path:\s*(\S+)", text, re.M)
    return m is not None and m.group(1) == (dest / "semconv").as_posix()


def _build_chain(semconv: Path, genai: Path, into: Path, final: Path) -> Path:
    """Copy semconv (without GenAI's directories) and GenAI into `into`, GenAI's manifest
    pointing at `final`/semconv (where the copy will be once `into` is renamed to `final`)."""
    replaced = {p.name for p in genai.iterdir() if p.is_dir()}
    sc, ga = into / "semconv", into / "genai"
    shutil.copytree(semconv, sc, ignore=lambda d, names: replaced & set(names) if Path(d) == semconv else ())
    shutil.copytree(genai, ga)
    _point_at(ga / "manifest.yaml", (ga / "manifest.yaml").read_text(encoding="utf-8"), final / "semconv")
    return ga


def _point_at(manifest: Path, text: str, target: Path) -> None:
    """Write a manifest whose (only) dependency is the local registry at target."""
    text, n = re.subn(r"^(\s*registry_path:\s*)\S+", lambda m: m.group(1) + target.as_posix(), text,
                      count=1, flags=re.M)
    if n != 1:
        raise CheckError(f"no registry_path in {manifest.name}")
    manifest.write_text(text, encoding="utf-8")


def _fields_registry(fields: Path, into: Path, base: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    (into / "fields.yaml").write_bytes(fields.read_bytes())
    (into / "manifest.yaml").write_text(
        "name: project\n"
        f"description: Project-only log fields from {fields.name}\n"
        # Must differ from logwarden's own schema_url, or Weaver sees a circular dependency.
        "schema_url: https://logwarden.invalid/project/0.0.0\n"
        "dependencies:\n"
        "  - schema_url: https://github.com/Ingeniator/logwarden/schemas/0.1.0\n"
        f"    registry_path: {base.as_posix()}\n",
        encoding="utf-8",
    )
    return into


def evaluate(text: str, *, fields: Path | None = None, registry: Path | None = None,
             out: Path | None = None, upstreams: list[str] | None = None,
             policy: Policy | None = None) -> tuple[list[dict], int, int]:
    """Run Weaver on log text: (all findings, JSON records, total lines). Raises CheckError."""
    found, inp = _evaluate(text, fields=fields, registry=registry, out=out,
                           upstreams=upstreams, policy=policy)
    return found, len(inp.records), inp.total


def registry_types(binary: str, registry: Path) -> dict[str, str]:
    """Attribute name -> registry type (int, double, boolean, string, string[], ...), for CSV values
    and empty lists."""
    proc = subprocess.run([binary, "registry", "resolve", "-r", str(registry), "--include-unreferenced",
                           "--format", "json", "--skip-policies", "--quiet"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        groups = json.loads(proc.stdout).get("groups") or []
    except ValueError as e:
        raise CheckError("weaver couldn't resolve the registry for CSV value types\n"
                         + proc.stderr[-2000:]) from e
    types: dict[str, str] = {}
    for g in groups:
        for a in g.get("attributes") or []:
            typ = a.get("type")
            if isinstance(typ, dict):   # enum: typed by its members' values
                vals = [m.get("value") for m in typ.get("members") or []]
                typ = ("int" if vals and all(isinstance(v, int) and not isinstance(v, bool) for v in vals)
                       else "double" if vals and all(isinstance(v, (int, float)) for v in vals) else "string")
            if isinstance(typ, str) and isinstance(a.get("name"), str):
                types.setdefault(a["name"], typ)
    return types


def _evaluate(text: str, *, fields: Path | None, registry: Path | None, out: Path | None,
              upstreams: list[str] | None, policy: Policy | None) -> tuple[list[dict], Input]:
    if fields and registry:
        raise CheckError("use fields or registry, not both")
    if fields and not fields.is_file():
        raise CheckError(f"no such fields file: {fields}")
    try:
        binary = weaver.find()
    except weaver.WeaverError as e:
        raise CheckError(str(e)) from e

    with tempfile.TemporaryDirectory(prefix="logwarden-") as tmp:
        work = out or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        if not registry:
            try:
                base = base_registry(Path(tmp) / "base")
            except weaver.WeaverError as e:
                raise CheckError(str(e)) from e
            registry = _fields_registry(fields, Path(tmp) / "registry", base) if fields else base
        reg = registry
        types = functools.cache(lambda: registry_types(binary, reg))
        inp = read(text, policy.source if policy else None, types)
        records, bad = inp.records, inp.bad
        # An empty list is typed from the registry, which costs one more Weaver call: only then.
        declared = types() if any(v == [] for r in records for v in r.obj.values()) else {}
        reports = live_check(binary, registry, [to_sample(r.obj, declared) for r in records], work,
                             Path(tmp), keep=out is not None)
        found = collect({"samples": reports}, records, bad, upstreams,
                        events=policy.events if policy else None,
                        events_path=policy.events_path if policy else None,
                        missing_columns=inp.missing_columns)
    return found, inp


CHUNK_MIN = 2000   # lines per Weaver process at least; fewer lines run in one process
MAX_JOBS = 8


def _jobs(n: int) -> int:
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    return max(1, min(cpus, MAX_JOBS, n // CHUNK_MIN))


def _advice(node: Any) -> list[list]:
    """Every all_advice list under node, in collect()'s order."""
    return [n["all_advice"] for n in _walk(node) if isinstance(n, dict) and "all_advice" in n]


def _plain(v: Any) -> bool:
    return not isinstance(v, (dict, list))


def _attribute_advice(x: dict) -> list[list]:
    """_advice of an attribute sample, read from where Weaver puts it (fast path)."""
    a = x.get("attribute")
    if isinstance(a, dict) and len(x) == 1 and all(_plain(v) for k, v in a.items() if k != "live_check_result"):
        r = a.get("live_check_result")
        if r is None:
            return []
        if isinstance(r, dict) and all(_plain(v) for k, v in r.items() if k != "all_advice"):
            return [r["all_advice"]] if "all_advice" in r else []
    return _advice(x)


_W3C = {32: re.compile(r"[0-9a-f]{32}"), 16: re.compile(r"[0-9a-f]{16}")}


def _trace_key(t: Any, n: int) -> Any:
    """What the trace-context rule sees of an id: a valid one is like any other valid one."""
    if isinstance(t, str) and _W3C[n].fullmatch(t) and t.strip("0"):
        return "valid"
    return t


def _shape(log: dict) -> str:
    """Everything the per-line (not per-field) rules read: same shape, same line advice."""
    return json.dumps([log["body"], log["severity_number"], log["severity_text"],
                       _trace_key(log["trace_id"], 32), _trace_key(log["span_id"], 16),
                       [a["name"] for a in log["attributes"]]], ensure_ascii=False, default=str)


def _weaver_run(binary: str, registry: Path, samples: Path, out: Path) -> tuple[Path, str]:
    proc = subprocess.run(
        [binary, "registry", "live-check",
         "--registry", str(registry),
         "--include-unreferenced",
         "--config", str(data.path("weaver.toml")),
         "--advice-policies", str(data.path("policies")),
         "--input-source", str(samples),
         "--input-format", "json",
         "--format", "json",
         "--output", str(out),
         "--quiet"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    log = proc.stdout + proc.stderr
    report = out / "live_check.json"
    if not report.is_file() or report.stat().st_size == 0:
        errors = [ln for ln in log.splitlines() if "×" in ln or "rror" in ln]
        raise CheckError("weaver produced no report (registry or fields file invalid?)\n"
                         + "\n".join(errors or log.splitlines()[-20:]))
    return report, log


def live_check(binary: str, registry: Path, samples: list[dict], work: Path, tmp: Path,
               keep: bool = False) -> list[dict]:
    """Weaver's advice for each log sample, in order: {"log": {"body", "advice": [all_advice, ...]}}.

    Weaver judges every field on its own, and the per-line rules read only the event, level,
    trace context and field names. So each distinct field (name, type, value) is sent once as an
    attribute sample, and each distinct line shape once as a log sample; each line's advice is
    put back together from them. Large inputs run in chunks, in parallel.
    work gets weaver.log, and with keep (-o) the samples.json and live_check.json Weaver saw."""
    from concurrent.futures import ThreadPoolExecutor
    unique: list[dict] = []
    index: dict[str, int] = {}
    plan = []   # per line: (shape sample index, [attribute sample index, ...])

    def add(key: str, sample: dict) -> int:
        if key not in index:
            index[key] = len(unique)
            unique.append(sample)
        return index[key]

    for sample in samples:
        log = sample["log"]
        attrs = [add(json.dumps(["a", a["name"], a["type"], a["value"]], ensure_ascii=False, default=str),
                     {"attribute": a}) for a in log["attributes"]]
        plan.append((add("l" + _shape(log), sample), attrs))

    jobs = _jobs(len(unique))
    size = -(-len(unique) // jobs)
    chunks = [unique[i:i + size] for i in range(0, len(unique), size)] or [[]]
    dirs = [work] if len(chunks) == 1 else [tmp / f"chunk-{i}" for i in range(len(chunks))]
    for d, chunk in zip(dirs, chunks):
        d.mkdir(parents=True, exist_ok=True)
        (d / "samples.json").write_text(json.dumps(chunk, ensure_ascii=False), encoding="utf-8")
    with ThreadPoolExecutor(len(chunks)) as pool:
        runs = list(pool.map(lambda d: _weaver_run(binary, registry, d / "samples.json", d), dirs))
    (work / "weaver.log").write_text("".join(log for _, log in runs), encoding="utf-8")
    merged = None
    if len(chunks) > 1 and keep:   # one samples.json and live_check.json in work, as with one process
        (work / "samples.json").write_text(json.dumps(unique, ensure_ascii=False), encoding="utf-8")
        merged = (work / "live_check.json").open("w", encoding="utf-8")
        merged.write('{"samples": [')
    advice: list[list[list]] = []   # per unique sample
    try:
        for i, (report, _) in enumerate(runs):
            got = json.loads(report.read_text(encoding="utf-8")).get("samples") or []
            if merged:
                merged.write(("," if i else "") + ",".join(json.dumps(x, ensure_ascii=False) for x in got))
            for x in got:
                if "log" in x:   # only the line's own advice: its fields come from attribute samples
                    log = x["log"] or {}
                    advice.append(_advice({k: v for k, v in log.items() if k != "attributes"}))
                else:
                    advice.append(_attribute_advice(x))
    finally:
        if merged:
            merged.write("]}")
            merged.close()
    if len(advice) != len(unique):
        raise CheckError(f"weaver reported {len(advice)} of {len(unique)} samples")
    # Fields first, then the line's own advice: the order of a report of the whole line.
    return [{"log": {"body": sample["log"]["body"],
                     "advice": [{"all_advice": a} for i in attrs for a in advice[i]]
                               + [{"all_advice": a} for a in advice[shape]]}}
            for sample, (shape, attrs) in zip(samples, plan)]


@dataclass
class Analysis:
    result: dict
    snapshot: dict
    events: dict          # catalog.collect() of the run
    violations: list[dict] = field(default_factory=list)   # after disable/baseline, all of them


def analyse(text: str, *, fields: Path | None = None, registry: Path | None = None,
            out: Path | None = None, policy: Policy | None = None,
            compare: dict | None = None, compare_path: str | None = None) -> Analysis:
    """Check log text, plus the run's snapshot and events (and the comparison with `compare`)."""
    from logwarden import catalog
    policy = policy or Policy()
    found, inp = _evaluate(text, fields=fields, registry=registry, out=out,
                           upstreams=policy.upstreams, policy=policy)
    records = inp.records
    lines: dict[str, int] = {}
    for r in [*inp.records, *inp.bad]:
        lines[r.service] = lines.get(r.service, 0) + 1
    result = summarise(found, len(records), inp.total, policy, lines, inp)
    events = catalog.collect(records)
    snap = make_snapshot(found, policy, events)
    if compare is not None:
        result["compare"] = compare_with(found, policy, events, compare, compare_path)
    if out:
        (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                         encoding="utf-8")
    return Analysis(result, snap, events, apply_policy(found, policy).violations)


def run(text: str, *, fields: Path | None = None, registry: Path | None = None,
        out: Path | None = None, policy: Policy | None = None) -> dict:
    """Check log text. Raises CheckError on setup problems."""
    return analyse(text, fields=fields, registry=registry, out=out, policy=policy).result


def make_snapshot(found: list[dict], policy: Policy | None, events: dict) -> dict:
    """What a run found, for `--compare` on another run: baseline entries plus event counts."""
    snap = make_baseline(found, policy)
    snap["kind"] = "snapshot"
    snap["about"] = ("What one logwarden run found. Compare another run with it: "
                     "logwarden check LOGS --compare FILE")
    snap["events"] = {name: e["count"] for name, e in events.items()}
    return snap


def load_compare(path: Path) -> dict:
    """A snapshot or a baseline file to compare with. Raises CheckError."""
    content = load_baseline(path)
    if "events" in content and not (isinstance(content["events"], dict)
                                    and all(isinstance(k, str) for k in content["events"])):
        raise CheckError(f"{path}: 'events' must map event names to counts")
    for e in content["entries"]:
        if not (isinstance(e, dict) and {"rule", "field", "logger", "event"} <= set(e)):
            raise CheckError(f"{path}: every entry needs rule, field, logger and event")
    return content


def compare_with(found: list[dict], policy: Policy, events: dict, base: dict,
                 base_path: str | None = None) -> dict:
    """New, fixed and unchanged findings (by baseline key, so moved lines match) and events."""
    first: dict[tuple, dict] = {}
    for v in violations_of([f for f in found if not policy.off(f["rule"])]):
        first.setdefault(fingerprint(v), v)
    known = {_entry_key({**e, "field": e["field"] or ""}): e.get("count", 1)
             for e in base["entries"] if not policy.off(e["rule"])}
    new = sorted((v for k, v in first.items() if k not in known),
                 key=lambda f: (f["line"] is None, f["line"] or 0))
    fixed = [{"rule": k[0], "field": k[1], "logger": k[2], "event": k[3], **({"kind": k[4]} if k[4] else {}),
              "count": n} for k, n in sorted(known.items()) if k not in first]
    base_events = base.get("events")
    return {
        "base": base_path,
        "new": len(new),
        "fixed": len(fixed),
        "unchanged": len(set(first) & set(known)),
        "new_findings": [_finding(f) for f in new[:MAX_FINDINGS]],
        "fixed_entries": fixed[:MAX_FINDINGS],
        "new_events": None if base_events is None else sorted(set(events) - set(base_events)),
        "gone_events": None if base_events is None else sorted(set(base_events) - set(events)),
    }
