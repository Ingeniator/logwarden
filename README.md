# logwarden

A guardrail for logs. logwarden reads the JSON logs your service actually writes and flags
every line that would make debugging harder: ad-hoc field names, missing service info,
free-text events, and errors that don't say what failed. It exits with code 1 on
problems, so CI and AI agents can use it the way they use tests.

It checks output, so it works for any language: JSON lines from a file or stdin, or logs sent
over OTLP by any OpenTelemetry SDK. `logwarden lint-code` checks source code too. Field names follow
[OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/),
the names traces and metrics already use, so logs, traces and metrics can be queried together.

```
$ uvx logwarden check logs.jsonl
logwarden: 573 records, 2843 violations

  539	adr_event_name - Event is a constant snake_case name
	fix: Use a fixed name like import_completed and move variable data into fields
	e.g. §4: event 'Loaded 3 models' must be a constant snake_case name

findings (first 20):
  line 1	adr_event_name	§4: event 'Loaded 3 models' must be a constant snake_case name
```

## What it checks

| Rule | Fails on |
|---|---|
| `adr_banned_synonym` | `status=500` instead of `http.response.status_code=500` |
| `adr_semconv_prefix` | `http_route` instead of `http.route` |
| `missing_attribute` | a field that isn't semconv and isn't declared by the project |
| `deprecated` | a renamed semconv field (`gen_ai.system`) |
| `adr_missing_required` | no `service.name`, `service.version` or `deployment.environment.name` |
| `adr_environment_value` | `dev` instead of `development` |
| `adr_event_name` | `event="Loaded 3 models"` instead of `models_loaded` |
| `adr_failure_context` | an error line without `exception.type`, `error.type` or a status code |
| `adr_sensitive_data` | `password=...`, a JWT, an API key, `user:pass@` in a URL, an email or card number |
| `adr_non_json_line` | a plain-text line (a library banner, a printed traceback) |
| `adr_trace_context` | a `trace_id`/`span_id` that isn't W3C lowercase hex |
| `adr_upstream_failure_coverage` | an upstream in `upstreams` whose failure path no test exercised |

The standard is in [docs/standard.md](https://github.com/Ingeniator/logwarden/blob/main/docs/standard.md), and each rule's fix is in
[docs/rules.md](https://github.com/Ingeniator/logwarden/blob/main/docs/rules.md).

## Use it in a project

```bash
uv add --dev logwarden                      # or: pipx install logwarden / uvx logwarden
logwarden init --agent                      # fields file + AGENTS.md section
LOGWARDEN_CAPTURE=logs.jsonl pytest         # capture logs (pytest plugin, auto-registered)
logwarden check logs.jsonl                  # check them
```

Other languages and Docker:

```bash
logwarden capture -o logs.jsonl -- npm test   # receive logs over OTLP while the command runs, then check
docker run --rm -v "$PWD:/work" ghcr.io/ingeniator/logwarden:0.6.0 check logs.jsonl   # no Python needed
```

Setup recipes whose output passes: [Node (pino)](https://github.com/Ingeniator/logwarden/blob/main/docs/recipes/node.md),
[Go (slog)](https://github.com/Ingeniator/logwarden/blob/main/docs/recipes/go.md),
[Java (logback)](https://github.com/Ingeniator/logwarden/blob/main/docs/recipes/java.md).

Python services can get compliant logs from one call, and test helpers for failure logs:

```python
from logwarden.logging import configure          # pip install 'logwarden[structlog]'
log = configure(service_name="billing-api", service_version="1.4.2", environment="production")
```

```yaml
# CI
- uses: Ingeniator/logwarden@v0.6.0
  with:
    logs: logs.jsonl
```

Also lint the source: `logwarden lint-code src/` runs logwarden's Semgrep rules (Python,
JavaScript/TypeScript, Go, Java) for formatted event names, secret and banned field names, and
`print` instead of the logger. Ruff `LOG`, `G` and `TRY400` rules for Python, and sloglint for Go,
cover more cases in an existing lint setup. The recommended config is in
[docs/integration.md §6](https://github.com/Ingeniator/logwarden/blob/main/docs/integration.md#6-lint-the-source-too-recommended).

Project settings go in `[tool.logwarden]` in `pyproject.toml`, or in `logwarden.toml`.
On an existing service, `logwarden baseline logs.jsonl` records today's findings so CI
fails only on new ones.
See [docs/integration.md](https://github.com/Ingeniator/logwarden/blob/main/docs/integration.md) for capturing logs from any stack,
project-only fields and migrating a legacy service.

## For AI agents

- **Claude Code:** `/plugin marketplace add Ingeniator/logwarden`, then
  `/plugin install logwarden@logwarden`. This gives a skill for writing good logs, the MCP
  server and `/logwarden:check`.
- **Other agents:** `logwarden init --agent` adds a logwarden section to `AGENTS.md`. The MCP
  server runs with `uvx --from 'logwarden[mcp]' logwarden mcp` and provides `check_logs`,
  `explain_rule`, `list_rules` and `get_standard`.
- **Machine output:** `logwarden check --json` gives each finding's line number, rule and
  fix hint.
- **Agents working on this repo:** read [AGENTS.md](https://github.com/Ingeniator/logwarden/blob/main/AGENTS.md).

## How it works

Each log line becomes a sample for [OTel Weaver](https://github.com/open-telemetry/weaver)
`registry live-check`. Weaver checks the sample's field names against the semconv
registry (plus logwarden's shared fields and the project's `logwarden-fields.yaml`) and
runs the rules in `src/logwarden/_data/policies/logwarden.rego`. `logwarden capture` turns
OTLP log records into the same JSON lines first. Python 3.10+ on Linux, macOS or Windows, or
the Docker image (linux/amd64, linux/arm64). Weaver and the semantic conventions are
downloaded on first use, checked against pinned SHA-256 sums and cached.

## Not covered yet

See [docs/roadmap.md](https://github.com/Ingeniator/logwarden/blob/main/docs/roadmap.md): an HTML report with a diff against the base
branch, a PR comment with new/fixed counts, an event-name catalog. Release notes are in
[CHANGELOG.md](https://github.com/Ingeniator/logwarden/blob/main/CHANGELOG.md).

## License

[Apache-2.0](https://github.com/Ingeniator/logwarden/blob/main/LICENSE)
