## Logging (logwarden)

This project follows the logging standard checked by
[logwarden](https://github.com/Ingeniator/logwarden). When you add or change code that logs:

- Use constant snake_case `event` names and OTel semconv field names
  (`http.response.status_code`, not `status`). Log exceptions with `exc_info=True`, never
  `error=str(e)`. Never log secrets, credentials, emails, prompts or request/response
  bodies. Every line must be JSON.
- Run `uvx logwarden lint-code <changed paths>` and fix its findings. If the project runs
  Ruff or golangci-lint, keep its logging rules passing (Ruff `LOG`, `G`, `TRY400`; sloglint).
- Check what the code actually logs before you finish:
  `LOGWARDEN_CAPTURE=/tmp/logs.jsonl pytest <tests>` and then
  `uvx logwarden check /tmp/logs.jsonl --json`, or use the `check_logs` MCP tool. Services
  with an OpenTelemetry SDK (any language): `uvx logwarden capture --json -- <test command>`.
- Fix findings from your own changes. Don't silence them: don't regenerate the baseline
  or add rules to `disable` unless asked. Project-only fields go in
  `logwarden-fields.yaml`, but only when no semconv name exists.
- If the config sets `events = "logwarden-events.toml"`, add each new event to that file with
  a description (`uvx logwarden catalog <logs> -o logwarden-events.toml` adds them).
- Details: `logwarden rules <id>` and https://github.com/Ingeniator/logwarden/blob/main/docs/rules.md.
