---
name: logwarden
description: Use when writing or changing code that logs (logger calls, logging setup, error handling around HTTP/LLM/DB calls), or when asked to check or improve a service's logs. Runs logwarden on the logs the code actually produces and fixes violations against the logging standard.
---

# logwarden: keep logs useful

logwarden checks real log output against a logging standard: OTel semantic-convention
field names, required fields, constant event names, and failures that say what failed.
Your job is to make sure the code you write produces logs that pass.

## While writing code

- `event` is a constant snake_case name: `log.info("import_completed", rows_imported=n)`.
  Never use f-strings or variables in the event.
- Use OTel semconv names: `http.response.status_code`, `http.route`, `server.address`,
  `error.type`, `user.id`, `gen_ai.request.model`. Never `status`, `path`, `host`, `model`, `err`.
  In structlog, pass dotted keys as `**{"http.route": route}`.
- In `except`, log once with `exc_info=True`. Never `error=str(e)`. Every warning/error
  line needs `exception.type` (from `exc_info`), `error.type` or
  `http.response.status_code`.
- For failed outbound calls, log `server.address`, `http.response.status_code`, `error.type`,
  `duration_ms`, `upstream_request_id` and a scrubbed `upstream_error_message` (at most
  500 chars). Never log the raw response body.
- Never log secrets, tokens, prompts or completions, chat content, or request/response
  bodies.
- `service.name`, `service.version` and `deployment.environment.name` come from the shared
  logging setup, never from call sites.

Full standard: the MCP tool `get_standard`, or https://github.com/Ingeniator/logwarden/blob/main/docs/standard.md

## After changing code: check the logs it produces

1. **Capture logs** from code paths you touched, including the failure paths:
   - Python + pytest: `LOGWARDEN_CAPTURE=/tmp/logs.jsonl pytest <tests you touched or added>`
     (works when `logwarden` is a dev dependency; otherwise
     `uv run --with logwarden pytest ...`, which adds it to the project environment for one run).
   - Anything else: run the service or a smoke test and save stdout to `/tmp/logs.jsonl`.
2. **Check** with either:
   - MCP: the `check_logs` tool (`path` or `lines`), provided by the logwarden plugin
   - CLI: `logwarden check /tmp/logs.jsonl --json`, or `uvx logwarden check ...` with no install
3. **Fix** each finding. `by_rule[].hint` says how; `logwarden rules <id>` or the MCP
   tool `explain_rule` gives an example. `findings[].line` is the line number in the log
   file, and `findings[].event` tells you which log call produced it.
4. Re-run until the lines your change produced are clean.

## Rules for you

- **Legacy code:** a service that isn't migrated yet may have hundreds of findings. Fix
  the ones from code you wrote or changed. Report the rest (counts by rule) and don't
  refactor unrelated logging unless asked.
- **Never silence a finding** by:
  - adding the field to `logwarden-fields.yaml` when a semconv name exists,
  - lowering a real failure to `info` to avoid `adr_failure_context`,
  - removing the log line,
  - editing logwarden's rules.
- Only add a field to `logwarden-fields.yaml` when it's a real domain field: no
  semconv equivalent, snake_case, no semconv prefix, and safe to log. Say so in your
  summary.
- If you can't produce logs at all (no tests, the service can't run), say so. Don't
  claim the logs are compliant.
