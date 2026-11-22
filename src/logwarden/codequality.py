"""GitLab Code Quality report (artifacts:reports:codequality).

https://docs.gitlab.com/ci/testing/code_quality/ : a JSON array; each entry has description,
check_name, fingerprint, severity and location.path (relative, no "./") with location.lines.begin.
GitLab compares fingerprints with the target branch to list new and resolved findings, and merges
entries with the same fingerprint, so each finding gets its own. Locations and fingerprints are
the ones SARIF uses. Never contains logged values.
"""
from __future__ import annotations

import json

from logwarden import sarif

SEVERITY = {"adr_sensitive_data": "critical"}      # everything else: major


def _entry(rule: str, text: str, path: str, line: int | None, seen: dict, *identity) -> dict:
    fp = sarif._fingerprints(seen, *identity)["primaryLocationLineHash"]
    return {"description": text, "check_name": rule, "fingerprint": sarif._fingerprint(fp),
            "severity": SEVERITY.get(rule, "major"), "categories": ["Style"],
            "location": {"path": path, "lines": {"begin": int(line or 1)}}}


def _in_repo(path: str | None) -> bool:
    return bool(path) and not path.startswith("file://")


def from_check(violations: list[dict], log_path: str | None = None, source_root: str | None = None) -> list[dict]:
    """Code Quality entries for runtime findings (`check --codequality`), after disable/baseline.

    GitLab needs a repository path: a source file outside the repository (a build path that
    --source-root didn't strip) falls back to the finding's line in the log file, and stdin to
    the name "logs".
    """
    log_uri = sarif.uri(log_path, None) if log_path else None
    log_uri = log_uri if _in_repo(log_uri) else None
    seen: dict[str, int] = {}
    out = []
    for v in violations:
        text = sarif.message(v)
        path, line, _ = sarif.locate(v, log_uri, source_root)
        if not _in_repo(path):
            if path:
                text += f" (logged at {path.removeprefix('file://')}:{line})"
            path, line = log_uri or "logs", v.get("file_line") or 1
        out.append(_entry(v["rule"], text, path, line, seen, *sarif.identity(v, log_uri, source_root)))
    return out


def from_lint(result: dict) -> list[dict]:
    """Code Quality entries for `lint-code --codequality` (the Semgrep findings)."""
    seen: dict[str, int] = {}
    return [_entry(f["rule"], f["message"].split("\n")[0], sarif.uri(f["path"]), f["line"], seen,
                   f["rule"], sarif.uri(f["path"]), (f.get("code") or "").strip())
            for f in result["findings"]]


def dumps(entries: list[dict]) -> str:
    return json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
