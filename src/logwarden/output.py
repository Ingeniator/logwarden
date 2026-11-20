"""Human-readable rendering of a check result."""
from __future__ import annotations

from logwarden import report


def _set(v) -> bool:  # jq truthiness: only null and false are false
    return v is not None and v is not False


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _first_line(s) -> str:
    return str(s).split("\n")[0]


def text(result: dict, max_findings: int = 20) -> str:
    out = []
    head = f"logwarden: {result['records']} records, {result['violations']} violations"
    if result["skipped_lines"] > 0:
        head += f" ({result['skipped_lines']} blank or non-JSON lines)"
    if result.get("baselined"):
        head += f", {result['baselined']} more known in baseline {result['baseline']}"
    out.append(head)
    c = result.get("compare")
    if c:
        line = f"compared with {c['base'] or 'base'}: {c['new']} new, {c['fixed']} fixed, {c['unchanged']} unchanged"
        if c.get("new_events"):
            line += f"; new events: {', '.join(c['new_events'][:10])}" + (" ..." if len(c["new_events"]) > 10 else "")
        out.append(line)
    if result["violations"] == 0:
        out.append("OK")
    for r in result["by_rule"]:
        block = f"\n  {r['count']}\t{r['rule']}"
        if _set(r.get("title")):
            block += f" - {r['title']}"
        if _set(r.get("hint")):
            block += f"\n\tfix: {r['hint']}"
        block += f"\n\te.g. {_first_line(r['example'])}"
        out.append(block)
    if report.show_services(result):
        out.append("\nby service:")
        for s in result["services"]:
            top = f"\ttop: {s['top_rule']}" if s["top_rule"] else ""
            out.append(f"  {s['service']}\t{_n(s['lines'], 'line')}\t{_n(s['violations'], 'violation')}\t"
                       f"{s['compliance']}% compliant lines{top}")
    if note := report.missing_note(result):
        out.append("\nnote: " + note.replace("`", ""))
    if result["violations"] > 0:
        out.append(f"\nfindings (first {min(max_findings, result['violations'])}):")
    for f in result["findings"][:max_findings]:
        where = f"line {f['line']}" if f["line"] is not None else "run"
        if f.get("id"):
            where += f" ({f['id']})"
        out.append(f"  {where}\t{f['rule']}\t{_first_line(f['message'])}")
    if result["violations"] > max_findings:
        out.append("  ... use --json or -o DIR for all")
    if result["advice"]:
        out.append("\nnot failing: " + ", ".join(f"{a['rule']}={a['count']}" for a in result["advice"]))
    for d in result.get("disabled") or []:
        out.append(f"disabled: {d['rule']} (not counted: {d['count']}): {d['reason']}")
    if result.get("baseline_fixed"):
        out.append(f"baseline: {result['baseline_fixed']} entries no longer occur; "
                   "run 'logwarden baseline' to shrink it")
    return "\n".join(out)
