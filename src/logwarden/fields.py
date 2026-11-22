"""`logwarden init --from LOGS`: declare the custom fields a service logs, from real logs.

Only fields that the registry doesn't know (`missing_attribute`) are declared. Fields that a
rule wants renamed or not logged at all (synonyms, underscore semconv names, sensitive data) are
listed in a comment instead, so they get fixed rather than accepted. The file never contains a
logged value: only field names, types and how many lines had them.
"""
from __future__ import annotations

import datetime
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from logwarden import check

GROUP = "registry.project.log"
_NOT_DECLARED = check.NOT_DECLARED
_ID = re.compile(r"^\s*-\s*id:\s*['\"]?([^'\"\s#]+)", re.M)


@dataclass
class Proposal:
    declare: dict[str, tuple[str, int]] = field(default_factory=dict)       # name -> (type, lines)
    skipped: dict[str, tuple[str, str, int]] = field(default_factory=dict)  # name -> (rule, why, lines)
    lines: int = 0


def _type(values: list) -> str:
    """The most common type; ints with doubles are double. Empty lists say only "a list"."""
    kinds = Counter(check._weaver_type(v) for v in values if v is not None and v != [])
    if not kinds:
        return "string[]" if [] in values else "string"
    for ints, doubles in (("int", "double"), ("int[]", "double[]")):
        if set(kinds) == {ints, doubles}:
            return doubles
    return kinds.most_common(1)[0][0]


def propose(found: list[dict], records: list[check.Record]) -> Proposal:
    """Fields to declare and fields to fix, from a standard-profile run's findings."""
    vs = check.violations_of(found)
    unknown = check.declarable(vs)   # a field a rule flags anywhere isn't declared, even where it passed
    skipped: dict[str, str] = {}
    for f in vs:
        if f.get("field") and f["rule"] in _NOT_DECLARED:
            skipped.setdefault(f["field"], f["rule"])
    seen: dict[str, list] = {}
    for r in records:
        for k, v in r.obj.items():
            if k in unknown or k in skipped:
                seen.setdefault(k, []).append(v)
    p = Proposal(lines=len(records))
    for name in sorted(unknown):
        vals = seen.get(name, [])
        p.declare[name] = (_type(vals), len(vals))
    for name, rule in sorted(skipped.items()):
        p.skipped[name] = (rule, _NOT_DECLARED[rule], len(seen.get(name, [])))
    return p


def declared(text: str) -> set[str]:
    """Attribute (and group) ids already in a fields file."""
    return set(_ID.findall(text))


def _comment(p: Proposal, names: list[str]) -> list[str]:
    if not names:
        return []
    out = ["# Not declared: a rule asks to rename these fields or not to log them. Fix them in the code."]
    for n in names:
        rule, why, lines = p.skipped[n]
        out.append(f"#   {n}: {rule} ({why}), {lines} line{'' if lines == 1 else 's'}")
    return out


def _group(p: Proposal, names: list[str], source: str, today: str, indent: str) -> list[str]:
    i2, i3 = indent + "  ", indent + "    "
    out = [f"{indent}- id: {GROUP}.{today.replace('-', '')}",
           f"{i2}type: attribute_group",
           f"{i2}display_name: project log fields ({today})",
           f"{i2}brief: Fields found by logwarden init --from {source} on {today}.",
           f"{i2}attributes:"]
    for n in names:
        typ, lines = p.declare[n]
        hint = "" if "." in n else f" A bare name: better namespaced (e.g. myservice.{n}) in the code."
        out += [f"{i3}- id: {n}",
                f"{i3}  type: {typ}",
                f"{i3}  stability: development",
                f'{i3}  brief: "TODO: what it is. Seen on {lines} of {p.lines} lines.{hint}"']
    return out


def write(path: Path, p: Proposal, source: str, today: str | None = None) -> tuple[list[str], list[str]]:
    """Add new fields (and the list of fields to fix) to `path`, keeping what's there.

    Returns (fields added, fields listed as not declared).
    """
    today = today or datetime.date.today().isoformat()
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    have = declared(text)
    new = [n for n in p.declare if n not in have]
    flagged = [n for n in p.skipped if f"#   {n}: " not in text]
    if not new and not flagged:
        return [], []
    if not text.strip():
        head = ["# Project-only log fields for logwarden (https://github.com/Ingeniator/logwarden).",
                f"# Written by `logwarden init --from {source}` on {today}. Review it: describe each",
                "# field, and remove any that OTel semantic conventions already have a name for",
                "# (https://opentelemetry.io/docs/specs/semconv/registry/attributes/).",
                "#"]
        body = _comment(p, flagged) + ["groups:"] + (_group(p, new, source, today, "  ") if new else [])
        if not new:
            body[-1] = "groups: []"
        path.write_text("\n".join(head + body) + "\n", encoding="utf-8")
        return new, flagged
    lines = text.rstrip("\n").split("\n")
    if new:
        empty = next((i for i, ln in enumerate(lines) if re.fullmatch(r"groups:\s*\[\]\s*", ln)), None)
        if empty is not None:          # the template: groups: []
            lines[empty:empty + 1] = ["groups:"] + _group(p, new, source, today, "  ")
        else:                          # append a group after the existing ones
            first = re.search(r"^(\s*)- id:", text, re.M)
            lines += _group(p, new, source, today, first.group(1) if first else "  ")
    if flagged:
        lines += [""] + _comment(p, flagged)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return new, flagged


def set_profile(cwd: Path, profile: str = "standard") -> str:
    """Set `profile` in logwarden.toml or [tool.logwarden]; create logwarden.toml if neither."""
    own, py = cwd / "logwarden.toml", cwd / "pyproject.toml"
    line = f'profile = "{profile}"'
    pat = re.compile(r'^profile\s*=.*$', re.M)
    if own.is_file():
        text = own.read_text(encoding="utf-8")
        if pat.search(text.split("\n[", 1)[0]):
            head, sep, rest = text.partition("\n[")
            text = pat.sub(line, head, count=1) + sep + rest
        else:
            text = line + "\n" + text
        own.write_text(text, encoding="utf-8")
        return f"set {line} in {own.name}"
    if py.is_file() and re.search(r"^\[tool\.logwarden\]\s*$", py.read_text(encoding="utf-8"), re.M):
        text = py.read_text(encoding="utf-8")
        start = re.search(r"^\[tool\.logwarden\]\s*$", text, re.M).end()
        nxt = re.search(r"^\[", text[start:], re.M)
        end = start + nxt.start() if nxt else len(text)
        section = text[start:end]
        section = pat.sub(line, section, count=1) if pat.search(section) else "\n" + line + section
        py.write_text(text[:start] + section + text[end:], encoding="utf-8")
        return f"set {line} in pyproject.toml [tool.logwarden]"
    own.write_text(line + "\n", encoding="utf-8")
    return f"created {own.name} with {line}"
