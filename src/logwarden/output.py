"""Human-readable rendering of a check result."""
from __future__ import annotations


def _set(v) -> bool:  # jq truthiness: only null and false are false
    return v is not None and v is not False


def _first_line(s) -> str:
    return str(s).split("\n")[0]


def text(result: dict, max_findings: int = 20) -> str:
    out = []
    head = f"logwarden: {result['records']} records, {result['violations']} violations"
    if result["skipped_lines"] > 0:
        head += f" ({result['skipped_lines']} non-JSON lines skipped)"
    out.append(head)
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
    if result["violations"] > 0:
        out.append(f"\nfindings (first {min(max_findings, result['violations'])}):")
    for f in result["findings"][:max_findings]:
        out.append(f"  line {f['line']}\t{f['rule']}\t{_first_line(f['message'])}")
    if result["violations"] > max_findings:
        out.append("  ... use --json or -o DIR for all")
    if result["advice"]:
        out.append("\nnot failing: " + ", ".join(f"{a['rule']}={a['count']}" for a in result["advice"]))
    return "\n".join(out)
