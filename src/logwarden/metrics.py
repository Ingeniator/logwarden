"""`logwarden check --signal metrics` (experimental, v0.12.0): Prometheus / OpenMetrics text.

The text is parsed, each metric family is translated back to its OTel name, unit and
instrument (OTel's Prometheus compatibility rules, matched against the registry), and sent to
Weaver live-check as metric samples, so Weaver's metric checks and logwarden's label rules
(banned synonyms, semconv prefixes, sensitive data) run on it. Cardinality is checked here:
it needs every series of a metric at once. Findings are grouped per metric, label and rule,
with the number of series, instead of one per series.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from logwarden import __version__, check, weaver

DEFAULT_SERIES_BUDGET = 1000
CARDINALITY = "adr_metric_cardinality"

# Prometheus unit suffix -> UCUM unit (OTel Prometheus compatibility spec, reversed).
UNITS = {
    "seconds": "s", "milliseconds": "ms", "microseconds": "us", "nanoseconds": "ns",
    "minutes": "min", "hours": "h", "days": "d",
    "bytes": "By", "kilobytes": "kBy", "megabytes": "MBy", "gigabytes": "GBy", "terabytes": "TBy",
    "kibibytes": "KiBy", "mebibytes": "MiBy", "gibibytes": "GiBy", "tebibytes": "TiBy",
    "meters": "m", "volts": "V", "amperes": "A", "joules": "J", "watts": "W", "grams": "g",
    "celsius": "Cel", "hertz": "Hz", "ratio": "1", "percent": "%",
}
# Hand-written short suffixes (`api_latency_ms`): not what an exporter writes, but read as a unit.
SHORT_UNITS = {"ms": "ms", "us": "us", "ns": "ns", "sec": "s", "secs": "s"}
_PER = {"second": "s", "minute": "min", "hour": "h", "day": "d", "week": "wk", "month": "mo", "year": "a"}

# Families that client libraries export on their own (process and runtime collectors): the
# service can't rename them, so they're not checked.
LIBRARY = re.compile(r"^(process_|python_|go_|jvm_|nodejs_|promhttp_|scrape_)|^(process|python|go|jvm|nodejs)\.")

# Labels that make a series per user, request or value (standard §6): ids, emails, full URLs
# and paths, queries, raw messages, timestamps, client addresses, user agents.
UNBOUNDED = re.compile(
    r"(?i)(^|[._])(id|uuid|guid|email|e_?mail|session|token)$"
    r"|^(url\.(full|path|query)|url_(full|path|query)|http\.(url|target)|http_(url|target)|path|uri|url|query)$"
    r"|(^|[._])(message|msg|error\.message|exception\.message|reason_text)$"
    r"|(^|[._])(timestamp|ts|time|date|datetime)$"
    r"|^(client\.address|client_address|client_ip|remote_addr|ip|user_agent|user_agent\.original)$")
BOUNDED_IDS = {"service.instance.id", "cpu.id"}   # identify an instance, not a user or request


class MetricsError(Exception):
    pass


@dataclass
class Family:
    name: str                       # as exposed (Prometheus family name, or dotted UTF-8)
    type: str = "untyped"
    unit: str | None = None         # from `# UNIT`, if given
    series: dict[tuple, dict] = field(default_factory=dict)   # label set -> {sample suffix: value | buckets}
    line: int = 0


# ---------------------------------------------------------------- parsing

_NAME = r'(?:[a-zA-Z_:][a-zA-Z0-9_:]*|"(?:[^"\\]|\\.)*")'
_SAMPLE = re.compile(r'^\s*(' + _NAME + r')?\s*(\{.*\})?\s*([^\s{}]+)(?:\s+\S+)?\s*$')
_LABEL = re.compile(r'\s*(' + _NAME + r')\s*(?:=\s*"((?:[^"\\]|\\.)*)")?\s*,?')


def _unquote(s: str) -> str:
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def _labels(body: str) -> tuple[str | None, dict[str, str]]:
    """`{a="1",b="2"}` or Prometheus 3's `{"metric.name", a="1"}` -> (quoted name, labels)."""
    inner = body.strip()[1:-1]
    name, labels, pos = None, {}, 0
    while pos < len(inner):
        m = _LABEL.match(inner, pos)
        if not m or m.end() == pos:
            raise MetricsError(f"can't read labels {body[:80]}")
        key, val = _unquote(m.group(1)), m.group(2)
        if val is None:
            name = key
        else:
            labels[key] = _unquote('"' + val + '"')
        pos = m.end()
    return name, labels


_SUFFIXES = {"histogram": ("_bucket", "_sum", "_count", "_created"),
             "summary": ("_sum", "_count", "_created"),
             "counter": ("_total", "_created"), "gaugehistogram": ("_bucket", "_gsum", "_gcount")}


def parse(text: str) -> list[Family]:
    families: dict[str, Family] = {}
    order: list[str] = []

    def fam(name: str, line: int) -> Family:
        if name not in families:
            families[name] = Family(name, line=line)
            order.append(name)
        return families[name]

    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line == "# EOF":
            continue
        if line.startswith("#"):
            parts = line[1:].split(None, 2)
            if len(parts) >= 3 and parts[0] in ("TYPE", "UNIT", "HELP"):
                name = _unquote(parts[1])
                f = fam(name, n)
                if parts[0] == "TYPE":
                    f.type = parts[2].strip().lower()
                elif parts[0] == "UNIT":
                    f.unit = parts[2].strip()
            continue
        m = _SAMPLE.match(line)
        if not m:
            raise MetricsError(f"line {n}: not a metric sample: {line[:80]}")
        sample_name = _unquote(m.group(1)) if m.group(1) else None
        qname, labels = _labels(m.group(2)) if m.group(2) else (None, {})
        sample_name = sample_name or qname
        if not sample_name:
            raise MetricsError(f"line {n}: no metric name")
        try:
            value = float(m.group(3))
        except ValueError as e:
            raise MetricsError(f"line {n}: bad value {m.group(3)!r}") from e
        f, suffix = _family_of(sample_name, families)
        if f is None:
            f, suffix = fam(sample_name, n), ""
        le = labels.pop("le", None) if f.type in ("histogram", "gaugehistogram") else None
        q = labels.pop("quantile", None) if f.type == "summary" else None
        key = tuple(sorted(labels.items()))
        s = f.series.setdefault(key, {})
        if suffix == "_bucket" and le is not None:
            s.setdefault("buckets", []).append((float(le), value))
        elif q is not None:
            s.setdefault("quantiles", []).append((float(q), value))
        else:
            s[suffix or "value"] = value
    # prometheus_client's text format exports `x_created` (the start time) as a gauge family of its
    # own next to each counter, histogram and summary: part of that metric, not one to check.
    out = []
    for k in order:
        if k.endswith("_created"):
            stem = k[: -len("_created")]
            if any(families.get(n) and families[n].type in ("counter", "histogram", "summary")
                   for n in (stem, stem + "_total")):
                continue
        out.append(families[k])
    return out


def _family_of(sample: str, families: dict[str, Family]) -> tuple[Family | None, str]:
    if sample in families and families[sample].type not in ("histogram", "summary", "gaugehistogram"):
        return families[sample], ""
    for suf in ("_bucket", "_sum", "_count", "_created", "_total", "_gsum", "_gcount"):
        if sample.endswith(suf) and (f := families.get(sample[: -len(suf)])) is not None:
            if suf in _SUFFIXES.get(f.type, ()) or (suf == "_total" and f.type in ("counter", "untyped")):
                return f, suf
    if sample in families:
        return families[sample], ""
    return None, ""


# ---------------------------------------------------------------- translation to OTel

@dataclass
class Registry:
    metrics: dict[str, dict]        # OTel metric name -> {"instrument", "unit"}
    attributes: dict[str, str]      # attribute name -> type (string, int, double, boolean, ...)

    def metric_for(self, base: str) -> str | None:
        if base in self.metrics:
            return base
        return self._prom_metrics.get(base)

    def attribute_for(self, label: str) -> str:
        if "." in label or label in self.attributes:
            return label
        return self._prom_attrs.get(label, label)

    def __post_init__(self):
        self._prom_metrics = {k.replace(".", "_"): k for k in sorted(self.metrics)}
        self._prom_attrs = {}
        for a in sorted(self.attributes):
            self._prom_attrs.setdefault(a.replace(".", "_"), a)


def load_registry(binary: str, registry: Path) -> Registry:
    proc = subprocess.run([binary, "registry", "resolve", "-r", str(registry), "--include-unreferenced",
                           "--format", "json", "--skip-policies", "--quiet"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        groups = json.loads(proc.stdout).get("groups") or []
    except ValueError as e:
        raise MetricsError("weaver couldn't resolve the registry\n" + proc.stderr[-2000:]) from e
    metrics, attrs = {}, {}
    for g in groups:
        if g.get("type") == "metric" and g.get("metric_name"):
            metrics[g["metric_name"]] = {"instrument": g.get("instrument"), "unit": g.get("unit") or ""}
        for a in g.get("attributes") or []:
            if isinstance(a.get("name"), str):
                typ = a.get("type")
                if isinstance(typ, dict):      # enum: typed by its members' values
                    vals = [m.get("value") for m in typ.get("members") or []]
                    typ = ("int" if vals and all(isinstance(v, int) and not isinstance(v, bool) for v in vals)
                           else "string")
                attrs.setdefault(a["name"], typ if isinstance(typ, str) else "string")
    return Registry(metrics, attrs)


def _unit_suffix(base: str) -> tuple[str, str | None]:
    """`x_seconds` -> (`x`, `s`); `x_bytes_per_second` -> (`x`, `By/s`)."""
    m = re.match(r"^(.*)_([a-z]+)_per_([a-z]+)$", base)
    if m and m.group(2) in UNITS and m.group(3) in _PER:
        return m.group(1), f"{UNITS[m.group(2)]}/{_PER[m.group(3)]}"
    for word, unit in {**UNITS, **SHORT_UNITS}.items():
        if base.endswith("_" + word) and len(base) > len(word) + 1:
            return base[: -len(word) - 1], unit
    return base, None


@dataclass
class Translated:
    family: Family
    otel: str                # name sent to Weaver
    known: bool              # a registry metric
    instrument: str
    unit: str
    labels: dict[str, str]   # exposed label -> attribute name
    semconv: str | None = None        # for an unknown metric: the semconv metric that covers it
    semconv_prom: str | None = None


def translate(f: Family, reg: Registry) -> Translated:
    base = f.name
    if "." not in base:
        if f.type == "counter" and base.endswith("_total"):
            base = base[: -len("_total")]
        base, unit = _unit_suffix(base)
        if f.unit:
            unit = UNITS.get(f.unit, f.unit)
        otel = reg.metric_for(base)
    else:
        unit = UNITS.get(f.unit, f.unit) if f.unit else None
        otel = base if base in reg.metrics else None
    known = otel is not None
    spec = reg.metrics.get(otel) if known else None
    if unit is None:
        # Prometheus drops annotation units ({request}) and the counter unit 1: take semconv's.
        unit = spec["unit"] if spec and (spec["unit"] in ("1", "") or spec["unit"].startswith("{")) else ""
    instrument = {"counter": "counter", "histogram": "histogram", "summary": "histogram",
                  "gaugehistogram": "histogram"}.get(f.type, "gauge")
    if instrument == "gauge" and spec and spec["instrument"] == "updowncounter":
        instrument = "updowncounter"     # Prometheus has no up-down counter: a gauge is fine
    labels = {k: reg.attribute_for(k) for key in f.series for k, _ in key}
    hint = None if known else semconv_hint(base.replace(".", "_"), reg)
    return Translated(f, otel if known else base, known, instrument, unit, labels, hint,
                      prom_name(hint, reg) if hint else None)


def prom_name(otel: str, reg: Registry) -> str:
    """The Prometheus name an exporter derives from a semconv metric."""
    spec = reg.metrics[otel]
    name = otel.replace(".", "_")
    suffix = {v: k for k, v in UNITS.items()}.get(spec["unit"])
    if suffix and not (suffix == "ratio" and spec["instrument"] != "gauge"):
        name += "_" + suffix
    if spec["instrument"] == "counter":
        name += "_total"
    return name


# ---------------------------------------------------------------- samples for Weaver

def _value_attr(name: str, v: str, typ: str | None) -> dict:
    """A label value is always text in Prometheus: typed as the registry types the attribute when
    it reads as that type (status code "200" -> int 200), else left as a string."""
    try:
        if typ == "int" and re.fullmatch(r"-?\d+", v):
            return {"name": name, "type": "int", "value": int(v)}
        if typ == "double":
            return {"name": name, "type": "double", "value": float(v)}
        if typ == "boolean" and v.lower() in ("true", "false"):
            return {"name": name, "type": "boolean", "value": v.lower() == "true"}
    except ValueError:
        pass
    return {"name": name, "type": "string", "value": v}


def to_sample(t: Translated, reg: Registry) -> dict:
    """One metric sample per family. Data points: per distinct set of label names, enough points
    that every distinct value of every label appears once (Weaver judges each attribute on its own;
    series counts are computed here)."""
    by_names: dict[tuple, list[tuple]] = defaultdict(list)
    for key in t.family.series:
        by_names[tuple(k for k, _ in key)].append(key)
    points = []
    for names, keys in by_names.items():
        values = {n: sorted({dict(k)[n] for k in keys}) for n in names}
        first = t.family.series[keys[0]]
        width = max((len(v) for v in values.values()), default=1)
        for i in range(max(width, 1)):
            attrs = [_value_attr(t.labels[n], values[n][i % len(values[n])], reg.attributes.get(t.labels[n]))
                     for n in names]
            points.append(_point(t.instrument, first, attrs))
    if not points:
        points.append(_point(t.instrument, {}, []))
    return {"metric": {"name": t.otel, "instrument": t.instrument, "unit": t.unit, "data_points": points}}


def _point(instrument: str, s: dict, attrs: list[dict]) -> dict:
    if instrument == "histogram":
        buckets = sorted(s.get("buckets") or [])
        bounds = [b for b, _ in buckets if b != float("inf")]
        counts, prev = [], 0.0
        for _, c in buckets:
            counts.append(max(int(c - prev), 0))
            prev = c
        count = int(s.get("_count", prev))
        return {"attributes": attrs, "count": count, "sum": float(s.get("_sum", 0.0)),
                "bucket_counts": counts or [count], "explicit_bounds": bounds}
    value = s.get("value", s.get("_total", 0.0))
    return {"attributes": attrs, "value": value}


# ---------------------------------------------------------------- cardinality

def cardinality(t: Translated, budget: int) -> list[dict]:
    out = []
    series = t.family.series
    distinct: dict[str, set[str]] = defaultdict(set)
    carrying: dict[str, int] = defaultdict(int)
    for key in series:
        for k, v in key:
            distinct[k].add(v)
            carrying[k] += 1
    for label in sorted(distinct):
        attr = t.labels[label]
        if attr in BOUNDED_IDS or not (UNBOUNDED.search(attr) or UNBOUNDED.search(label)):
            continue
        out.append(_finding(CARDINALITY, "violation", t, label,
                            f"label '{label}' is unbounded (one series per user, request or value: "
                            f"{_n(len(distinct[label]), 'distinct value')} now); put it in logs or traces, "
                            "not in a metric label", carrying[label]))
    if len(series) > budget:
        top = max(distinct, key=lambda k: len(distinct[k])) if distinct else None
        detail = f"; most distinct values: '{top}' ({len(distinct[top])})" if top else ""
        out.append(_finding(CARDINALITY, "violation", t, top,
                            f"{len(series)} series, over the budget of {budget}{detail}", len(series)))
    return out


# ---------------------------------------------------------------- findings

# Common Prometheus-style names and the semconv metric that covers them (the hint on
# missing_metric). Matched on the name without unit and _total suffixes.
SEMCONV_FOR = [
    (re.compile(r"^(http_)?(server_)?requests?(_duration|_latency|_time)?$|^http_server_duration$"),
     "http.server.request.duration"),
    (re.compile(r"^http_(server_)?requests?_(duration|latency|time)$"), "http.server.request.duration"),
    (re.compile(r"^(api|endpoint|route|handler|server)_(request_)?(duration|latency|time)$"),
     "http.server.request.duration"),
    (re.compile(r"^http_(server_)?request_(body_)?size$"), "http.server.request.body.size"),
    (re.compile(r"^http_(server_)?response_(body_)?size$"), "http.server.response.body.size"),
    (re.compile(r"^http_client_requests?(_duration|_latency)?$"), "http.client.request.duration"),
    (re.compile(r"^(http_)?(server_)?(active|inflight|in_flight)_requests$|^http_requests_in_progress$"),
     "http.server.active_requests"),
    (re.compile(r"(^|_)db_(client_)?(query|queries|operation)s?(_duration|_latency|_time)?$"),
     "db.client.operation.duration"),
    (re.compile(r"(^|_)(llm|gen_?ai)_.*token"), "gen_ai.client.token.usage"),
    (re.compile(r"(^|_)(llm|gen_?ai)_(requests?|calls?|operations?)(_duration|_latency)?$"),
     "gen_ai.client.operation.duration"),
]


def semconv_hint(base: str, reg: Registry) -> str | None:
    for rx, name in SEMCONV_FOR:
        if rx.search(base) and name in reg.metrics:
            return name
    return None


HINTS = {
    "missing_metric": "Not a semconv metric. If semconv has one for this, use it (its Prometheus name "
                      "is derived from the OTel name); a domain metric goes in the project registry "
                      "(logwarden-fields.yaml, a group with type: metric).",
    "unit_mismatch": "Set the instrument's unit to the semconv unit (durations in s, sizes in By); "
                     "the exporter adds the Prometheus suffix.",
    "unexpected_instrument": "Use the instrument semconv defines for this metric (durations: histogram).",
    "required_attribute_not_present": "Add the label semconv requires on this metric.",
    "missing_attribute": "Use the semconv attribute name if there is one, or declare the label in the "
                         "project registry.",
    CARDINALITY: "Keep labels bounded (route templates, status codes, a fixed set of kinds); "
                 "per-user or per-request detail belongs in logs or traces.",
}
# Rule ids metrics add to the log rules (experimental; `disable` accepts them).
RULES = {CARDINALITY, "missing_metric", "unit_mismatch", "unexpected_instrument",
         "required_attribute_not_present"}
_SKIP = re.compile(r"^entity_")      # resource checks are v0.13.0


def _finding(rule, level, t: Translated, label, message, series) -> dict:
    return {"rule": rule, "level": level, "metric": t.family.name, "otel_name": t.otel,
            "field": label, "message": message, "series": series, "line": t.family.line}


def _collect(report: dict, translated: list[Translated]) -> list[dict]:
    grouped: dict[tuple, dict] = {}
    for t, sample in zip(translated, report.get("samples") or []):
        exposed = {v: k for k, v in t.labels.items()}
        carrying: dict[str, int] = defaultdict(int)
        for key in t.family.series:
            for k, _ in key:
                carrying[t.labels[k]] += 1
        for node in check._walk(sample):
            if not (isinstance(node, dict) and "all_advice" in node):
                continue
            for a in node["all_advice"] or []:
                rule = check.a_id(a)
                if not rule or _SKIP.match(rule):
                    continue
                attr = (a.get("context") or {}).get("attribute_key")
                label = exposed.get(attr, attr)
                msg = (a.get("message") or "").replace("; don't log it", "; don't put it in a metric label")
                if rule == "missing_metric" and t.semconv:
                    msg += f" Semconv has {t.semconv} for this: use it (exported as {t.semconv_prom})."
                key = (rule, t.family.name, label, msg)
                if key not in grouped:
                    series = carrying.get(attr, 0) if attr in carrying else len(t.family.series) or 1
                    grouped[key] = _finding(rule, a.get("level") or a.get("advice_level"), t, label, msg, series)
    found = list(grouped.values())
    # A label reported as a synonym, a wrong prefix or a secret by name must go: "unknown" is noise.
    renamed = {(f["metric"], f["field"]) for f in found if f["rule"] in check._NAMING_RULES}
    return [f for f in found if not (f["rule"] == "missing_attribute" and (f["metric"], f["field"]) in renamed)]


def run(text: str, *, fields: Path | None = None, registry: Path | None = None,
        series_budget: int = DEFAULT_SERIES_BUDGET, disabled: set[str] | None = None) -> dict:
    try:
        families = parse(text)
    except MetricsError as e:
        raise check.CheckError(f"metrics: {e}") from e
    checked = [f for f in families if not LIBRARY.search(f.name)]
    try:
        binary = weaver.find()
    except weaver.WeaverError as e:
        raise check.CheckError(str(e)) from e
    with tempfile.TemporaryDirectory(prefix="logwarden-") as tmp:
        if registry is None:
            try:
                base = check.base_registry(Path(tmp) / "base")
            except weaver.WeaverError as e:
                raise check.CheckError(str(e)) from e
            registry = check._fields_registry(fields, Path(tmp) / "registry", base) if fields else base
        try:
            reg = load_registry(binary, registry)
        except MetricsError as e:
            raise check.CheckError(str(e)) from e
        translated = [translate(f, reg) for f in checked]
        found = []
        if translated:
            samples = Path(tmp) / "samples.json"
            samples.write_text(json.dumps([to_sample(t, reg) for t in translated]), encoding="utf-8")
            out = Path(tmp) / "out"
            out.mkdir()
            report_path, _ = check._weaver_run(binary, registry, samples, out)
            found = _collect(json.loads(report_path.read_text(encoding="utf-8")), translated)
        for t in translated:
            found += cardinality(t, series_budget)
    off = disabled or set()
    found = [f for f in found if f["rule"] not in off]
    violations = [f for f in found if f["level"] == "violation"]
    violations.sort(key=lambda f: (f["line"], f["metric"], f["rule"], f["field"] or ""))
    advice: dict[str, int] = defaultdict(int)
    for f in found:
        if f["level"] != "violation":
            advice[f["rule"]] += 1
    by_rule: dict[str, int] = defaultdict(int)
    for v in violations:
        by_rule[v["rule"]] += 1
    return {
        "tool": "logwarden", "version": __version__, "signal": "metrics", "experimental": True,
        "ok": not violations,
        "metrics": len(checked),
        "series": sum(len(f.series) for f in checked),
        "library_metrics_skipped": len(families) - len(checked),
        "semconv_metrics": sum(t.known for t in translated),
        "violations": len(violations),
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: (-kv[1], kv[0]))),
        "findings": [{k: v[k] for k in ("rule", "metric", "otel_name", "field", "series", "message")}
                     for v in violations],
        "advice": [{"rule": r, "count": n} for r, n in sorted(advice.items())],
        "series_budget": series_budget,
        "hints": {r: HINTS[r] for r in by_rule if r in HINTS},
    }


def text(r: dict, limit: int = 50) -> str:
    out = [f"logwarden (metrics, experimental): {r['metrics']} metrics, {r['series']} series, "
           f"{r['violations']} findings"
           + (f"; {r['library_metrics_skipped']} library metrics not checked" if r["library_metrics_skipped"] else "")]
    if r["by_rule"]:
        out.append("  by rule: " + ", ".join(f"{k}={v}" for k, v in r["by_rule"].items()))
    for rule, hint in r["hints"].items():
        out.append(f"\n{rule}\n\tfix: {hint}")
    if r["findings"]:
        out.append(f"\nfindings (first {min(limit, len(r['findings']))}):")
        for f in r["findings"][:limit]:
            where = f["metric"] + (f" {{{f['field']}}}" if f["field"] else "")
            out.append(f"  {where}\t{f['rule']}\t{f['message']} ({f['series']} series)")
    if r["advice"]:
        out.append("\nnot failing: " + ", ".join(f"{a['rule']}={a['count']}" for a in r["advice"]))
    return "\n".join(out)


def markdown(r: dict, limit: int = 50) -> str:
    out = [f"### logwarden (metrics, experimental): {r['violations']} findings in {r['metrics']} metrics",
           "", "| Metric | Label | Rule | Series | Message |", "|---|---|---|---|---|"]
    esc = lambda s: str(s).replace("|", "\\|")
    for f in r["findings"][:limit]:
        out.append(f"| `{esc(f['metric'])}` | {('`' + esc(f['field']) + '`') if f['field'] else ''} | "
                   f"`{f['rule']}` | {f['series']} | {esc(f['message'])} |")
    if len(r["findings"]) > limit:
        out.append(f"\n… {len(r['findings']) - limit} more (`--json` for all)")
    out.append(f"\n<sub>logwarden {__version__}</sub>")
    return "\n".join(out) + "\n"


def _n(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")
