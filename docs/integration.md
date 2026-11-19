# Connecting a project to logwarden

Four steps: install, capture logs, check in CI, set up agents. Each step is independent,
and most projects need about 10 lines of config. A source linter (§6) is a recommended
addition.

## 1. Install

```bash
uv add --dev logwarden          # Python projects: CLI + pytest capture plugin
pipx install logwarden          # or a global CLI
uvx logwarden check logs.jsonl  # or no install at all (needs uv)

logwarden init --agent          # logwarden-fields.yaml + a logwarden section in AGENTS.md
```

Weaver, which does the actual checking, and the OpenTelemetry semantic conventions are
downloaded on first run into the user cache directory (`logwarden setup` does it ahead of
time). After that, checks work offline. Set `WEAVER` to use your own binary.

**No Python on the machine?** Use the Docker image, which has Weaver and the semantic
conventions inside:

```bash
docker run --rm -v "$PWD:/work" ghcr.io/ingeniator/logwarden:0.6.0 check logs.jsonl
```

## Node, Go, Java: setup recipes

Logger configuration whose output passes the check, each with a runnable example that CI
checks: [Node (pino)](recipes/node.md), [Go (`log/slog`)](recipes/go.md),
[Java (logback)](recipes/java.md). Other languages: write one JSON object per line with the
fields in §3 of the standard, or send logs over OTLP (below).

## Python: make compliant logs the default

```bash
uv add 'logwarden[structlog]'
```

```python
from logwarden.logging import configure, extract_error_code, extract_error_message
log = configure(service_name="billing-api", service_version="1.4.2", environment="production")

except httpx.HTTPStatusError as e:
    log.error("upstream_call_failed", exc_info=True,
              upstream_error_message=extract_error_message(e.response),
              **{"server.address": e.request.url.host,
                 "http.response.status_code": e.response.status_code,
                 "error.type": extract_error_code(e.response) or type(e).__name__})
```

`configure()` adds the required fields, trace context, `exception.*` fields and redaction,
and routes third-party loggers through the same JSON formatter (run uvicorn with
`log_config=None`). Name, version and environment can come from `OTEL_SERVICE_NAME`,
`SERVICE_VERSION` and `DEPLOYMENT_ENVIRONMENT`. A missing value or an environment like `dev`
raises at startup.

In tests, `logwarden.testing` checks the log lines a test produces:

```python
from logwarden.testing import capture, assert_failure_logged, assert_logs_clean

with capture() as logs:
    client.post("/chat")                       # the upstream is mocked to time out
assert_failure_logged(logs, "upstream_call_failed", **{"server.address": "api.openai.com"})
assert_logs_clean(logs)                        # the full logwarden check on these lines
```

## 2. Capture the logs your code produces

logwarden checks real output, so the project needs a run that produces logs: tests,
or a smoke run. Include failure cases (500s, timeouts). §10 of the standard is
about exactly those lines.

**Python / pytest.** With `logwarden` installed (as a dev dependency), the capture plugin is
registered automatically:

```bash
LOGWARDEN_CAPTURE=logs.jsonl pytest
# without adding a dependency:
LOGWARDEN_CAPTURE=logs.jsonl uv run --with logwarden pytest
```

The plugin does nothing unless `LOGWARDEN_CAPTURE` is set. It writes each log line the
service formats to that file, at `INFO` and above (`LOGWARDEN_LEVEL` changes this). It
works with structlog routed through stdlib `logging`, which is the setup in the standard.

**Containers (any language):**

```bash
docker compose up -d myservice && ./smoke-test.sh
docker compose logs --no-log-prefix --no-color myservice > logs.jsonl
```

**Anything else:** save the service's stdout to a file, or pipe it in:
`./my-service 2>&1 | logwarden check -`. Every non-blank line must be a JSON object
(`adr_non_json_line`), so capture only the service's output, not a test runner's.

**Any language with an OpenTelemetry SDK: capture over OTLP.** `logwarden capture` is an
OTLP/HTTP receiver. It writes each log record as a JSON line (resource attributes merged in,
`event` from the event name or string body, `level` from the severity, `logger` from the
instrumentation scope) and then runs the same check:

```bash
logwarden capture -o logs.jsonl -- npm test        # runs the command with OTEL_* set, then checks
logwarden capture -o logs.jsonl                    # or receive on :4318 until Ctrl-C / SIGTERM
docker run --rm -p 4318:4318 -v "$PWD:/work" ghcr.io/ingeniator/logwarden:0.6.0 capture --host 0.0.0.0
```

With a command, it sets `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_LOGS_EXPORTER=otlp` and the
protocol `http/protobuf` (gRPC isn't supported; an existing `http/json` setting is kept).
The exit code is the command's if it failed, otherwise the check's. The command's own stdout
goes to stderr, so `--json` output stays parseable. Traces and metrics sent to the same
endpoint are accepted and ignored.

## 3. Check in CI

GitHub Actions:

```yaml
- run: LOGWARDEN_CAPTURE=logs.jsonl uv run pytest
- uses: Ingeniator/logwarden@v0.6.0
  with:
    logs: logs.jsonl
    # fail: "false"     # report only, while a legacy service is being migrated
```

Other CI systems: `uvx logwarden==0.6.0 check logs.jsonl`. The exit code is 0 when the logs
are clean, 1 when there are violations, and 2 when setup failed.

**Migrating an existing service:** record a baseline so CI fails only on new problems,
then shrink it:

```bash
LOGWARDEN_CAPTURE=logs.jsonl pytest
logwarden baseline logs.jsonl            # writes .logwarden-baseline.json
```

```toml
[tool.logwarden]
baseline = ".logwarden-baseline.json"     # commit it with the config
```

Fix the logging setup first (it clears `adr_missing_required` on every line at once), then
the call sites. When `check` reports baseline entries that no longer occur, run
`logwarden baseline` again so the file only shrinks. A finding matches its baseline entry by
rule, field, logger and event name (numbers and ids masked), so code moving around doesn't
break it. `fail: "false"` in the Action still works for a report-only start.

## 4. Project config and project-only fields

```toml
# pyproject.toml (or the same keys at top level in logwarden.toml)
[tool.logwarden]
fields = "logwarden-fields.yaml"
```

```toml
baseline = ".logwarden-baseline.json"             # optional, see "Migrating" above
disable = { adr_sensitive_data = "user.email is required by the audit log (SEC-12)" }
```

```toml
upstreams = ["api.openai.com", "api.anthropic.com"]   # each needs a failure test
```

`upstreams` lists the hosts the service calls. `check` fails when a test run has no failure
line for one of them (`adr_upstream_failure_coverage`).

`disable` switches a rule off for this project. Each rule needs a reason, and disabled
rules are listed with their reasons in every result.

`fields` lists fields only this service logs. When there's no config, `./logwarden-fields.yaml`
is used if it exists. The template that `logwarden init` creates explains the format and
when a field belongs there. Fields that several services share go into logwarden's
shared registry instead, through a PR to this repo.

The order of precedence is `--fields`, then `$LOGWARDEN_FIELDS`, then the config, then
`./logwarden-fields.yaml`. Unknown config keys produce a warning.

## 5. AI agents

Agents should check the logs of the code they write, the same way they run tests.

| Agent | Setup |
|---|---|
| Claude Code | `/plugin marketplace add Ingeniator/logwarden`, then `/plugin install logwarden@logwarden`. This gives the skill, the MCP server (via `uvx`, so uv must be installed) and `/logwarden:check` |
| Other agents (Codex, Cursor, …) | `logwarden init --agent` adds a logwarden section to `AGENTS.md`. For MCP: `uvx --from 'logwarden[mcp]' logwarden mcp` |

MCP tools: `check_logs(path | lines)`, `explain_rule(rule_id)`, `list_rules()`,
`get_standard()`. Agents with shell access can use `logwarden check --json` instead. Both
give the same result.

The skill tells the agent to fix findings in its own changes, never silence them,
and not to rewrite unrelated legacy logging unless asked.

## 6. Lint the source too (recommended)

logwarden checks the logs a run produces, so it only sees code paths that ran. A source
linter catches some of the same mistakes in every file, before anything runs. Run both:
the linter in your normal lint step, and logwarden on the captured logs.

**Any language: `logwarden lint-code`** runs logwarden's [Semgrep](https://semgrep.dev)
rules on Python, JavaScript/TypeScript, Go and Java:

```bash
logwarden lint-code src/                     # exit 0 clean, 1 findings, 2 setup error
logwarden lint-code --json src/              # for CI tools and agents
logwarden lint-code --exclude-rule logwarden-print-python src/
```

| Rule (one per language: `-python`, `-js`, `-go`, `-java`) | Catches | Runtime rule it prevents |
|---|---|---|
| `logwarden-event-name-*` | a formatted or non-snake_case log message (`f"..."`, `%`, `.format`, `+`, template literal, `fmt.Sprintf`, `String.format`, `"Imported {} rows"`) | `adr_event_name` |
| `logwarden-sensitive-key-*` | a field named like `password`, `token`, `api_key` | `adr_sensitive_data` |
| `logwarden-banned-key-*` | a banned synonym as a field name (`error=`, `"err"`, `kv("status", ...)`) | `adr_banned_synonym` |
| `logwarden-print-*` | `print`, `console.log`, `fmt.Println`, `System.out.println` (warning; skips tests, scripts and `cli.py`) | `adr_non_json_line` |

Semgrep isn't a dependency of logwarden (it pins its own OpenTelemetry and `mcp` versions).
`lint-code` uses `semgrep` from `PATH`, or runs it with `uvx` in its own environment when uv
is installed. Semgrep has no native Windows build; use WSL or the Docker image. Like
Semgrep, `lint-code` skips `tests/` directories.

The linters below cover more cases for one language each, and fit an existing lint setup.

**Python: Ruff.** It works for stdlib `logging` and for structlog loggers such as
`log = structlog.get_logger()`.

```toml
# pyproject.toml
[tool.ruff.lint]
extend-select = ["LOG", "G", "TRY400"]
```

| Ruff rule | Catches | logwarden rule it prevents |
|---|---|---|
| `G001`–`G004` | f-string, `%` or `.format()` in the log message | `adr_event_name` |
| `TRY400` | `logger.error(...)` in `except` without the traceback (use `logger.exception`) | `adr_failure_context` |
| `LOG014` | `exc_info=` outside an exception handler | (logs an empty traceback) |
| `LOG015` | logging on the root logger | (no `logger` name) |
| `G010` | deprecated `logger.warn` | |

If your logger object has another name, list it in Ruff's `lint.logger-objects` setting.

**Go: sloglint** (part of golangci-lint) for `log/slog`:

```yaml
# .golangci.yaml
version: "2"
linters:
  enable: [sloglint]
  settings:
    sloglint:
      static-msg: true      # constant message = event name (adr_event_name)
      no-mixed-args: true
      forbidden-keys: [status, status_code, path, method, url, host, model,
                       traceback, err, error, exception, uid, env, headers]  # adr_banned_synonym
```

Don't enable `key-naming-case: snake`. It treats `.` as a word separator, so it flags the
correct semconv key `http.route` and its autofix rewrites it to `http_route`, which
logwarden then reports as `adr_semconv_prefix`. The `forbidden-keys` list is logwarden's
banned-synonym list (§2 of the standard); if it changes, update it here too.

A source linter can't see fields added by processors or middleware, or check that required
fields and failure context are actually present, so it doesn't replace logwarden.
