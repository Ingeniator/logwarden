"""logwarden MCP server: lets an AI agent check logs and look up the logging standard.

Run on stdio:  logwarden mcp          (needs the extra: pip install 'logwarden[mcp]')
Claude Code:   the logwarden plugin configures it, or
               claude mcp add logwarden -- uvx --from 'logwarden[mcp]' logwarden mcp

Results are the same as `logwarden check --json`. Relative paths and the project's
config/fields file resolve against the server's working directory (the project root).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from logwarden import check, cli, data

server = MCPServer(
    "logwarden",
    instructions=(
        "Guardrail for structured logs. After changing code that logs, capture the "
        "logs it produces (tests or a smoke run) and call check_logs. Fix every "
        "violation using the rule hints; call explain_rule for details and "
        "get_standard for the full logging standard. Don't declare fields in "
        "logwarden-fields.yaml just to pass: prefer OTel semconv names."
    ),
)


def _as_jsonl(lines: str | dict | list) -> str:
    # The SDK may hand over a JSON-looking string already parsed into dict/list.
    items = lines if isinstance(lines, list) else [lines]
    return "\n".join(i if isinstance(i, str) else json.dumps(i) for i in items) + "\n"


@server.tool()
def check_logs(path: str | None = None,
               lines: str | dict | list[str | dict] | None = None,
               fields_file: str | None = None) -> dict:
    """Check JSON-lines logs against the logging standard.

    Give either `path` (a log file) or `lines`: log lines as text (one JSON object
    per line), a single log object, or a list of lines/objects. `fields_file`
    declares project-only fields; by default the project's config or
    ./logwarden-fields.yaml is used.

    Returns ok, violations, by_rule (count, title, fix hint per rule) and
    findings (input line, event, rule, field, message).
    """
    if (path is None) == (lines is None):
        return {"error": "give exactly one of path or lines"}
    try:
        if path is not None:
            p = Path(path)
            if not p.is_file():
                return {"error": f"no such file: {path}"}
            text = p.read_bytes().decode("utf-8", errors="replace")
        else:
            text = _as_jsonl(lines)
        fields = cli.resolve_fields(fields_file, None, Path.cwd())
        return check.run(text, fields=fields)
    except (cli.UsageError, check.CheckError) as e:
        return {"error": str(e)}


@server.tool()
def list_rules() -> dict:
    """All rule ids with their standard section, title and fix hint."""
    return data.rules()


@server.tool()
def explain_rule(rule_id: str) -> str:
    """Full explanation of one rule: what fails, a bad/good example and the fix."""
    text = data.doc("rules.md")
    m = re.search(rf"^## {re.escape(rule_id)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not m:
        return f"unknown rule '{rule_id}'. Known: {', '.join(data.rules())}"
    return f"## {rule_id}\n{m.group(1).strip()}"


@server.tool()
def get_standard() -> str:
    """The logging standard (field names, required fields, levels, failures)."""
    return data.doc("standard.md")


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
