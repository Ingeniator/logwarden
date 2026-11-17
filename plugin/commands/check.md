---
description: Capture the logs this project produces and check them with logwarden
argument-hint: "[log file or test path]"
---

Check this project's logs against the logwarden logging standard.

1. If `$ARGUMENTS` is a log file, check it. Otherwise capture logs first:
   - Python with pytest: `LOGWARDEN_CAPTURE=/tmp/logwarden-logs.jsonl uv run --with logwarden pytest $ARGUMENTS`
     (or plain `pytest` if `logwarden` is already a dev dependency).
   - Other stacks: run the service or a smoke test and save stdout to `/tmp/logwarden-logs.jsonl`.
2. Check with the `check_logs` MCP tool (`path`), or `uvx logwarden check <file> --json`.
3. Report violations grouped by rule, with the fix hint for each, and say which findings
   come from code changed in this session.
4. Offer to fix the findings in code changed in this session. Don't silence findings, and
   don't add fields to `logwarden-fields.yaml` when an OTel semconv name exists.
