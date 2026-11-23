"""SARIF 2.1.0 for GitHub code scanning (and other SARIF viewers).

Runtime findings go on the source line that logged them, from the line's
code.file.path / code.line.number (semconv), else on their line in the log file.
Like every report, SARIF never contains logged values: only rule messages, event and field names.
"""
from __future__ import annotations

import hashlib
import os
from urllib.parse import quote
from pathlib import Path

from logwarden import __version__, data, report

SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFO_URI = "https://github.com/Ingeniator/logwarden"
LINT_DOC = "docs/integration.md#6-lint-the-source-too-recommended"


def uri(path: str, root: str | None = None) -> str:
    """Repository-relative URI: strip `root` (default: the current directory) and './'."""
    p = path.replace("\\", "/")
    if os.path.isabs(path) or p.startswith("/"):
        try:
            rel = os.path.relpath(path, root or os.getcwd()).replace("\\", "/")
        except ValueError:      # another drive on Windows
            rel = None
        if rel and rel != ".." and not rel.startswith("../"):
            return rel
        if p.startswith("/"):      # a POSIX path (also when the logs come from Linux and we run on Windows)
            return "file://" + quote(p)
        return Path(path).as_uri()
    p = p.removeprefix("./")
    if root and not os.path.isabs(root):
        p = p.removeprefix(root.replace("\\", "/").removeprefix("./").strip("/") + "/")
    return p


def _location(path: str, line: int | None) -> dict:
    loc: dict = {"artifactLocation": {"uri": path}}
    if line:
        loc["region"] = {"startLine": line}
    return {"physicalLocation": loc}


def _rule(rule_id: str) -> dict:
    meta = data.rules().get(rule_id, {})
    r: dict = {"id": rule_id, "name": rule_id,
               "defaultConfiguration": {"level": "error"},
               "properties": {"tags": ["logging", "observability"]}}
    if meta.get("title"):
        r["shortDescription"] = {"text": meta["title"]}
    if meta.get("hint"):
        r["fullDescription"] = {"text": meta["hint"]}
        r["help"] = {"text": meta["hint"]}
    if url := report.doc_url(meta.get("doc")):
        r["helpUri"] = url
    if meta.get("section"):
        r["properties"]["tags"].append(meta["section"])
    return r


def _fingerprint(*parts) -> str:
    return hashlib.sha256("\x1f".join("" if p is None else str(p) for p in parts).encode("utf-8")).hexdigest()


def _fingerprints(seen: dict[str, int], *parts) -> dict:
    """Stable across commits: no line numbers, so an alert follows its log call when code moves.

    primaryLocationLineHash is set too, or upload-sarif computes one from the code around the
    line, and an unrelated edit next to the call would then look like a new alert.
    """
    h = _fingerprint(*parts)
    seen[h] = seen.get(h, 0) + 1
    return {"logwarden/v1": h, "primaryLocationLineHash": f"{h[:16]}:{seen[h]}"}


def _doc(rules: list[dict], results: list[dict], name: str) -> dict:
    return {"$schema": SCHEMA, "version": "2.1.0", "runs": [{
        "tool": {"driver": {"name": name, "version": __version__, "informationUri": INFO_URI,
                            "rules": rules}},
        "columnKind": "utf16CodeUnits",
        "results": results,
    }]}


def message(v: dict) -> str:
    """The rule message plus where it was found in the logs (never a logged value)."""
    text = v["message"] or v["rule"]
    if v.get("line") is not None:
        text += f" (log line {v['line']}" + (f", event '{v['event']}'" if v.get("event") else "") + ")"
    return text


def locate(v: dict, log_uri: str | None, source_root: str | None) -> tuple[str | None, int | None, str | None]:
    """(path, line, function) of a runtime finding: the source line that logged it, else its log line."""
    if loc := v.get("loc"):
        return uri(loc[0], source_root), loc[1], loc[2] or None
    if log_uri:
        # Search hits have no line in the file; viewers need a line, so use the first.
        return log_uri, v.get("file_line") or 1, None
    return None, None, None


def identity(v: dict, log_uri: str | None, source_root: str | None) -> tuple:
    """What makes a finding the same finding across commits: no line numbers, so it follows moved code."""
    path, _, func = locate(v, log_uri, source_root)
    where = (path, func) if v.get("loc") else (path, None)
    kind = (v["kind"],) if v.get("kind") else ()   # secret findings: what was found
    return (v["rule"], v.get("field"), v.get("logger"), v.get("fp_event"), *where, *kind)


def from_check(violations: list[dict], log_path: str | None = None, source_root: str | None = None) -> dict:
    """SARIF for runtime findings (`check --sarif`): the violations after disable/baseline."""
    ids = sorted({v["rule"] for v in violations}, key=str)
    index = {r: i for i, r in enumerate(ids)}
    log_uri = uri(log_path, None) if log_path else None
    results: list[dict] = []
    seen: dict[str, int] = {}
    for v in violations:
        path, line, func = locate(v, log_uri, source_root)
        res = {"ruleId": v["rule"], "ruleIndex": index[v["rule"]], "level": "error",
               "message": {"text": message(v)},
               "locations": [_location(path, line)] if path else [],
               "partialFingerprints": _fingerprints(seen, *identity(v, log_uri, source_root))}
        if func:
            res["locations"][0]["logicalLocations"] = [{"fullyQualifiedName": func, "kind": "function"}]
        results.append(res)
    return _doc([_rule(r) for r in ids], results, "logwarden")


def from_lint(result: dict) -> dict:
    """SARIF for `lint-code --sarif` (the Semgrep findings)."""
    findings = result["findings"]
    ids = sorted({f["rule"] for f in findings})
    index = {r: i for i, r in enumerate(ids)}
    first = {}
    for f in findings:
        first.setdefault(f["rule"], f["message"])
    help_uri = report.doc_url(LINT_DOC)
    seen: dict[str, int] = {}
    rules = [{"id": r, "name": r, "shortDescription": {"text": first[r].split("\n")[0][:200]},
              "helpUri": help_uri, "defaultConfiguration": {"level": "error"},
              "properties": {"tags": ["logging"]}} for r in ids]
    results = [{"ruleId": f["rule"], "ruleIndex": index[f["rule"]], "level": "error",
                "message": {"text": f["message"]},
                "locations": [_location(uri(f["path"]), f["line"])],
                "partialFingerprints": _fingerprints(seen, f["rule"], uri(f["path"]), (f.get("code") or "").strip())}
               for f in findings]
    return _doc(rules, results, "logwarden lint-code")
