# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2,<3"]
# ///
"""logwarden MCP server: lets an AI agent check logs and look up the logging standard.

Run (stdio):  uv run --script /path/to/logwarden/mcp/server.py
Claude Code:  claude mcp add logwarden -- uv run --script /path/to/logwarden/mcp/server.py

It wraps bin/logwarden, so results match the CLI and CI exactly. Relative paths
and the default logwarden-fields.yaml resolve against the server's working
directory (normally the project root the agent was started in).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "logwarden"

mcp = MCPServer(
    "logwarden",
    instructions=(
        "Guardrail for structured logs. After changing code that logs, capture the "
        "logs it produces (tests or a smoke run) and call check_logs. Fix every "
        "violation using the rule hints; call explain_rule for details and "
        "get_standard for the full logging standard. Don't declare fields in "
        "logwarden-fields.yaml just to pass: prefer OTel semconv names."
    ),
)


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(CLI), *args], input=stdin, capture_output=True, text=True,
        cwd=os.getcwd(), timeout=300,
    )


def _as_jsonl(lines: str | dict | list) -> str:
    # The SDK may hand over a JSON-looking string already parsed into dict/list.
    items = lines if isinstance(lines, list) else [lines]
    return "\n".join(i if isinstance(i, str) else json.dumps(i) for i in items) + "\n"


@mcp.tool()
def check_logs(path: str | None = None,
               lines: str | dict | list[str | dict] | None = None,
               fields_file: str | None = None) -> dict:
    """Check JSON-lines logs against the logging standard.

    Give either `path` (a log file) or `lines`: log lines as text (one JSON object
    per line), a single log object, or a list of lines/objects. `fields_file` declares project-only fields; by default
    ./logwarden-fields.yaml is used when it exists.

    Returns ok, violations, by_rule (count, title, fix hint per rule) and
    findings (input line, event, rule, field, message).
    """
    if bool(path) == bool(lines):
        return {"error": "give exactly one of path or lines"}
    args = ["check", path or "-", "--json"]
    stdin = _as_jsonl(lines) if lines is not None else None
    if fields_file:
        args += ["--fields", fields_file]
    proc = _run(args, stdin=stdin)
    if proc.returncode in (0, 1) and proc.stdout.strip():
        return json.loads(proc.stdout)
    return {"error": (proc.stderr or proc.stdout).strip()[-2000:]}


@mcp.tool()
def list_rules() -> dict:
    """All rule ids with their standard section, title and fix hint."""
    return json.loads((ROOT / "lib" / "rules.json").read_text())


@mcp.tool()
def explain_rule(rule_id: str) -> str:
    """Full explanation of one rule: what fails, a bad/good example and the fix."""
    text = (ROOT / "docs" / "rules.md").read_text()
    m = re.search(rf"^## {re.escape(rule_id)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not m:
        return f"unknown rule '{rule_id}'. Known: {', '.join(list_rules())}"
    return f"## {rule_id}\n{m.group(1).strip()}"


@mcp.tool()
def get_standard() -> str:
    """The logging standard (field names, required fields, levels, failures)."""
    return (ROOT / "docs" / "standard.md").read_text()


if __name__ == "__main__":
    mcp.run()
