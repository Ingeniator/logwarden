## Logging (logwarden)

This project follows the logging standard checked by
[logwarden](https://github.com/Ingeniator/logwarden). When you add or change code that logs:

- Use constant snake_case `event` names and OTel semconv field names
  (`http.response.status_code`, not `status`). Log exceptions with `exc_info=True`, never
  `error=str(e)`. Never log secrets, prompts or request/response bodies.
- Check what the code actually logs before you finish:
  `LOGWARDEN_CAPTURE=/tmp/logs.jsonl pytest <tests>` and then
  `logwarden check /tmp/logs.jsonl --json`, or use the `check_logs` MCP tool.
- Fix findings from your own changes. Don't silence them. Project-only fields go
  in `logwarden-fields.yaml`, but only when no semconv name exists.
- Details: `.claude/skills/logwarden/SKILL.md`, `logwarden rules <id>`.
