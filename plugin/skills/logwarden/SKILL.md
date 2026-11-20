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
- Never log secrets, tokens, credentials (also not inside a URL or a nested dict), email
  addresses, prompts or completions, chat content, or request/response bodies. Log ids,
  sizes and counts. `trace_id`/`span_id` come from the active span in W3C hex, or not at all.
- Every line is JSON: route third-party loggers through the formatter, and never `print()`.
- Run `logwarden lint-code <changed paths> --json` (Python, JS/TS, Go, Java; uses Semgrep,
  via uvx if it isn't installed) and fix its findings. If the project uses Ruff or
  golangci-lint, run it too; the recommended logging rules (Ruff `LOG`, `G`, `TRY400`;
  sloglint) are in docs/integration.md §6 of the logwarden repo.
- Node, Go and Java services: follow the setup recipes (docs/recipes/ in the logwarden repo)
  instead of inventing a logger configuration.
- `service.name`, `service.version` and `deployment.environment.name` come from the shared
  logging setup, never from call sites. In a Python service without a compliant setup,
  suggest `logwarden.logging.configure(...)` (`logwarden[structlog]`); in one that already
  uses it, don't add a second setup.
- For failed upstream calls, use `extract_error_code(response)` and
  `extract_error_message(response)` from `logwarden.logging` instead of logging the body.
- When you add an upstream call, add a test where it fails and assert the failure line with
  `logwarden.testing.assert_failure_logged`.

Full standard: the MCP tool `get_standard`, or https://github.com/Ingeniator/logwarden/blob/main/docs/standard.md

## After changing code: check the logs it produces

1. **Capture logs** from code paths you touched, including the failure paths:
   - Python + pytest: `LOGWARDEN_CAPTURE=/tmp/logs.jsonl pytest <tests you touched or added>`
     (works when `logwarden` is a dev dependency; otherwise
     `uv run --with logwarden pytest ...`, which adds it to the project environment for one run).
   - A service with an OpenTelemetry SDK (any language):
     `logwarden capture -o /tmp/logs.jsonl --json -- <test command>` receives its logs over
     OTLP and checks them in one step.
   - Anything else: run the service or a smoke test and save stdout to `/tmp/logs.jsonl`.
2. **Check** with either:
   - MCP: the `check_logs` tool (`path` or `lines`), provided by the logwarden plugin
   - CLI: `logwarden check /tmp/logs.jsonl --json`, or `uvx logwarden check ...` with no install
3. **Fix** each finding. `by_rule[].hint` says how; `logwarden rules <id>` or the MCP
   tool `explain_rule` gives an example. `findings[].line` is the line number in the log
   file, and `findings[].event` tells you which log call produced it. When the lines carry
   `code.file.path`/`code.line.number` (Python `configure()` adds them under pytest), go
   straight to that file and line; `check --sarif FILE` lists them too.
4. Re-run until the lines your change produced are clean.

## Rules for you

- **Legacy code:** a service that isn't migrated yet may have hundreds of findings. Fix
  the ones from code you wrote or changed. Report the rest (counts by rule) and don't
  refactor unrelated logging unless asked.
- **Never silence a finding** by:
  - adding the field to `logwarden-fields.yaml` when a semconv name exists,
  - lowering a real failure to `info` to avoid `adr_failure_context`,
  - removing the log line,
  - editing logwarden's rules,
  - running `logwarden baseline` or adding a rule to `disable` in the project config.
    Those are the maintainers' decisions: suggest them in your summary if they seem right,
    but only make them when asked.
- If the project has a baseline, `check` only shows new findings. That's expected: fix
  what your change introduced.
- **Event catalog:** if the project config sets `events = "logwarden-events.toml"`,
  `adr_event_catalog` reports events that aren't listed. When you add an event on purpose,
  add it to that file with a one-line description (by hand, or with
  `logwarden catalog <logs> -o logwarden-events.toml`) and mention it in your summary.
  Prefer an existing event over a near-duplicate name; if the finding is a typo, fix the name.
- **Production logs:** when asked to audit what services log, use a Kibana CSV export
  (all fields) or an Elasticsearch `_search` response: `logwarden check export.json --json`.
  For Fluent Bit records add `--message-field log` and `--ignore-field kubernetes` (or the
  project's `[source]` config). Summarise by `services[]` (compliance, top rule) and never
  quote logged values from the export.
- To show what your change did to the logs, compare with a run on the base branch:
  `logwarden check <base logs> --snapshot /tmp/base.json`, then
  `logwarden check <logs> --compare /tmp/base.json --json` (`compare.new_findings`,
  `compare.fixed_entries`, `compare.new_events`).
- Only add a field to `logwarden-fields.yaml` when it's a real domain field: no
  semconv equivalent, snake_case, no semconv prefix, and safe to log. Say so in your
  summary.
- If you can't produce logs at all (no tests, the service can't run), say so. Don't
  claim the logs are compliant.
