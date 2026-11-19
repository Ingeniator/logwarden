"""`logwarden lint-code`: run logwarden's Semgrep rules on source code.

Semgrep is not a dependency: it pins its own versions of OpenTelemetry and mcp, which would
clash with a project's. It is used from $SEMGREP or PATH, else run in its own environment with
`uvx semgrep` when uv is installed, else lint-code exits 2 with an install hint.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from logwarden import __version__, data

SEMGREP_VERSION = "1.186.0"   # used with uvx when Semgrep isn't installed
INSTALL_HINT = ("Semgrep is needed for lint-code: install uv (lint-code then runs it with uvx), or "
                "'uv tool install semgrep' / 'pipx install semgrep' / 'brew install semgrep'. Semgrep has "
                "no native Windows build: use WSL or the Docker image there")


class LintError(RuntimeError):
    """Setup problem (no Semgrep, Semgrep crashed). Exit code 2."""


def rules_path() -> Path:
    return data.path("semgrep/logwarden.yml")


def rule_ids() -> set[str]:
    import re
    return set(re.findall(r"^  - id: (logwarden-[\w-]+)$", rules_path().read_text(encoding="utf-8"), re.M))


def find_semgrep() -> list[str]:
    """Command that runs Semgrep."""
    if env := os.environ.get("SEMGREP"):
        return [env]
    if found := shutil.which("semgrep"):
        return [found]
    here = Path(sys.executable).parent
    for name in ("semgrep", "semgrep.exe"):
        if (here / name).is_file():
            return [str(here / name)]
    if sys.platform != "win32" and (uvx := shutil.which("uvx")):
        return [uvx, "--quiet", f"semgrep=={SEMGREP_VERSION}"]
    raise LintError(INSTALL_HINT)


def _source_line(path: str, line: int) -> str:
    """The flagged line (Semgrep without a login returns "requires login" instead)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, text in enumerate(f, 1):
                if i == line:
                    return text.strip()
    except OSError:
        pass
    return ""


def run(paths: list[str], exclude: list[str] | None = None) -> dict:
    """Run the rules on `paths`; return {tool, version, ok, findings, errors}."""
    cmd = [*find_semgrep(), "scan", "--config", str(rules_path()), "--json", "--metrics=off",
           "--disable-version-check", "--quiet", *(paths or ["."])]
    env = {**os.environ, "SEMGREP_SEND_METRICS": "off"}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    except OSError as e:
        raise LintError(f"can't run semgrep: {e}. {INSTALL_HINT}") from e
    try:
        report = json.loads(proc.stdout)
    except ValueError as e:
        raise LintError(f"semgrep failed (exit {proc.returncode}): {proc.stderr.strip()[-2000:]}") from e
    findings = [{
        "rule": r["check_id"].rsplit(".", 1)[-1],
        "path": r["path"],
        "line": r["start"]["line"],
        "message": r["extra"]["message"],
        "code": _source_line(r["path"], r["start"]["line"]),
    } for r in report.get("results", [])]
    findings = [f for f in findings if f["rule"] not in set(exclude or [])]
    findings.sort(key=lambda f: (f["path"], f["line"], f["rule"]))
    errors = [e.get("message", str(e)) for e in report.get("errors", [])
              if e.get("level", "error") == "error"]
    if errors and not report.get("results") and proc.returncode not in (0, 1):
        raise LintError("semgrep: " + "; ".join(errors)[:2000])
    return {"tool": "logwarden lint-code", "version": __version__, "ok": not findings,
            "findings": findings, "errors": errors}


def text(result: dict, limit: int = 50) -> str:
    fs = result["findings"]
    lines = [f"logwarden lint-code: {len(fs)} findings"]
    for f in fs[:limit]:
        lines.append(f"  {f['path']}:{f['line']}\t{f['rule']}\t{f['message']}")
        if f["code"]:
            lines.append(f"      {f['code'].splitlines()[0][:160]}")
    if len(fs) > limit:
        lines.append(f"  ... {len(fs) - limit} more (--json for all)")
    for e in result["errors"][:5]:
        lines.append(f"  semgrep warning: {e.splitlines()[0][:200]}")
    if not fs:
        lines.append("OK")
    return "\n".join(lines)
