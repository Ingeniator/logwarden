# logwarden

**Tests for your logs.** logwarden checks the logs your services actually write against one
logging standard, built on OpenTelemetry semantic conventions. It fails CI on a bad line the
way a test fails on a bug, so logs stay searchable, safe and useful when something breaks.

[![PyPI](https://img.shields.io/pypi/v/logwarden)](https://pypi.org/project/logwarden/)
[![Python](https://img.shields.io/pypi/pyversions/logwarden)](https://pypi.org/project/logwarden/)
[![CI](https://github.com/Ingeniator/logwarden/actions/workflows/ci.yml/badge.svg)](https://github.com/Ingeniator/logwarden/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/Ingeniator/logwarden/blob/main/LICENSE)

## Start here

Pick the path that matches what you have. Each one ends with a first report in about 10
minutes, and starts with the `essential` rules: no secrets or personal data in logs, every line
is JSON, and every line says which service, version and environment wrote it.

| You have | Start with | First result |
|---|---|---|
| Logs in Elasticsearch or Kibana | [Elasticsearch / Kibana](https://github.com/Ingeniator/logwarden/blob/main/docs/start/elasticsearch.md) | A report on the last hour of production logs. No code change. |
| GitLab CI | [GitLab](https://github.com/Ingeniator/logwarden/blob/main/docs/start/gitlab.md) | New and fixed findings in every merge request. |
| GitHub Actions | [GitHub](https://github.com/Ingeniator/logwarden/blob/main/docs/start/github.md) | A comment on every pull request. |
| An AI coding agent | [AI agents](https://github.com/Ingeniator/logwarden/blob/main/docs/start/agents.md) | An agent that logs to the standard and checks its own work. |

Every report starts with **Fix first**: the few places in the code that cause most of the
violations. What stays stable between versions is in [STABILITY.md](https://github.com/Ingeniator/logwarden/blob/main/STABILITY.md).

```bash
uvx logwarden check logs.jsonl --profile essential   # or any file of JSON lines, in any language
```

## Why

Logs get written by many people, in many services, and then mostly read during an incident.
Code review rarely catches a bad log line, because reviewers see the code, not what it writes.
So the same problems turn up in almost every codebase:

- **Every service names things differently.** It's `status`, `statusCode`, `http_status` or
  `response.code`, so a query or a dashboard works for one service and silently misses the
  others.
- **Event names hold data.** `"Loaded 3 models"` and `"Loaded 4 models"` are different events,
  so you can't count them, alert on them or group by them.
- **Errors don't say what failed.** `upstream_call_failed` with no exception type, error type
  or status code means reproducing the bug just to read its cause.
- **Secrets and personal data leak.** A `password` field, a JWT, an API key or a customer's
  email ends up in the log store, where far more people can read it.
- **Logs can't be joined with traces and metrics,** because their field names don't match
  the ones OpenTelemetry uses everywhere else.

logwarden makes these problems fail the build. It checks **output, not code**, so it sees what
your logging setup, middleware and third-party libraries really produce, in any language.

## What a check looks like

This line looks normal:

```json
{"timestamp":"2026-11-14T10:00:00Z","level":"info","event":"Loaded 3 models","logger":"x","status":200,"http_route":"/a","service.name":"yallmp","deployment.environment.name":"dev"}
```

logwarden finds five problems in it, each with the fix:

```
$ logwarden check logs.jsonl
  1	adr_banned_synonym - Banned synonym for an OTel semconv field
	fix: Rename the field to the semconv name given in the message.
	e.g. §2: 'status' is a banned synonym; use http.response.status_code

  1	adr_event_name - Event is a constant snake_case name
	fix: Use a fixed name like import_completed and move variable data into fields: log.info("import_completed", rows_imported=n).
	e.g. §4: event 'Loaded 3 models' must be a constant snake_case name

  1	adr_semconv_prefix - Semconv name written with underscores
	e.g. §2: 'http_route' uses '_' where semconv uses '.'; probably 'http.route'
  ...
```

The fixed line, which passes:

```json
{"timestamp":"2026-11-14T10:00:00Z","level":"info","event":"models_loaded","logger":"app.models","http.response.status_code":200,"http.route":"/a","service.name":"yallmp","service.version":"0.1.0","deployment.environment.name":"development"}
```

The exit code is 0 when the logs pass, 1 when they have violations and 2 on a usage error, so
CI, scripts and AI agents can rely on it.

## What you get

- **One vocabulary across services.** Field names come from the
  [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/),
  the same names your traces and metrics use. Queries, dashboards and alerts written once work
  for every service.
- **Every failure can be diagnosed from its log line.** Error lines must say what failed, and
  logwarden can check that each upstream's failure path is exercised by a test.
- **No secrets in the log store.** Secret field names and value patterns (JWTs, API keys,
  credentials in URLs, emails, card numbers) fail the check. The Python setup redacts them
  before they're written.
- **Adoption without a big bang.** A baseline records today's findings, so CI fails only on
  new ones while you fix the old ones.
- **Compliance you can measure.** Every result is broken down by service, and production logs
  exported from Kibana or Elasticsearch can be checked the same way as test logs.
- **Reviewable changes to logging.** A PR comment shows the new and fixed findings compared
  with the base branch, and which event names a change adds or removes.
- **AI agents that log well.** A Claude Code plugin, an MCP server and an `AGENTS.md` section
  teach coding agents the standard and let them check their own work.

## More ways to use it

The [Start here](#start-here) paths are the quickest way in. Everything below is what logwarden
can do once it runs.

### 1. Gate pull requests in CI

Run the tests with log capture, then let the GitHub Action check the logs. On a pull request
it compares the run with the base branch, posts one comment that it updates on each push,
and uploads an HTML report.

```yaml
permissions:
  contents: read
  pull-requests: write          # only for comment: "true"
steps:
  - run: LOGWARDEN_CAPTURE=logs.jsonl pytest
  - uses: Ingeniator/logwarden@v0.11.0
    with:
      logs: logs.jsonl
      comment: "true"
```

With `sarif: "true"` (and `security-events: write`), GitHub code scanning shows each finding
on the source line that logged it, right in the PR diff. Python's `configure()` and the Go
recipe add the call-site fields that make this work.

On GitLab, the [CI template](https://github.com/Ingeniator/logwarden/blob/main/ci/gitlab/logwarden.gitlab-ci.yml)
runs the Docker image (no internet access needed) and writes a Code Quality report, so the
merge request lists new and resolved findings. Other CI systems run the same CLI with
`--snapshot`, `--compare`, `--markdown` and `--html`; see
[docs/integration.md](https://github.com/Ingeniator/logwarden/blob/main/docs/integration.md).

### 2. Adopt it on an existing service

```bash
logwarden baseline logs.jsonl       # record today's findings in .logwarden-baseline.json
logwarden check logs.jsonl          # from now on, fail only on new findings
```

Domain fields with no semconv name go in `logwarden-fields.yaml`. A rule you can't follow yet
can be disabled in config, but only with a written reason.

### 3. Any language, any stack

logwarden checks output, so the language doesn't matter.

```bash
logwarden capture -o logs.jsonl -- npm test    # receive OTLP logs from any OTel SDK while the command runs
docker run --rm -v "$PWD:/work" ghcr.io/ingeniator/logwarden:0.11.0 check logs.jsonl   # no Python needed
```

There are setup recipes, with example output that passes, for
[Node (pino)](https://github.com/Ingeniator/logwarden/blob/main/docs/recipes/node.md),
[Go (slog)](https://github.com/Ingeniator/logwarden/blob/main/docs/recipes/go.md) and
[Java (logback)](https://github.com/Ingeniator/logwarden/blob/main/docs/recipes/java.md).

### 4. Make good logs the default in Python

Optional: the check reads whatever JSON your own logger writes, so keep your setup if it
already produces compliant lines. For new services, or to fix an old setup, one call sets up structlog and the standard library so that every line passes: required
service fields, trace context, exceptions as semconv fields, redaction and quiet noisy
libraries.

```python
from logwarden.logging import configure          # pip install 'logwarden[structlog]'
log = configure(service_name="billing-api", service_version="1.4.2", environment="production")
log.info("invoice_sent", **{"invoice.id": inv.id})
```

The pytest plugin captures logs from any test run (`LOGWARDEN_CAPTURE=logs.jsonl pytest`), and
`logwarden.testing` provides `assert_failure_logged` and `assert_logs_clean` for asserting
on logs in tests.

### 5. Review what a change does to the logs

```bash
logwarden check logs.jsonl --snapshot base.json          # on main
logwarden check logs.jsonl --compare base.json --html report.html   # on a branch: new and fixed
logwarden catalog logs.jsonl -o logwarden-events.toml    # the events a service emits, to commit
```

With an event catalog configured, `check` reports any event that isn't in it, so dashboards and
alerts that match on event names don't break without anyone noticing.

### 6. Catch problems before anything runs

```bash
logwarden lint-code src/
```

This runs logwarden's Semgrep rules for Python, JavaScript/TypeScript, Go and Java: formatted
event names, secret and banned field names, and `print` instead of the logger. `--sarif`
writes the findings for code scanning, and a pre-commit hook (`logwarden-lint-code`) runs it
on staged files. Ruff (`LOG`,
`G`, `TRY400`) and sloglint cover more in an existing lint setup; the recommended config is in
[docs/integration.md §6](https://github.com/Ingeniator/logwarden/blob/main/docs/integration.md#6-lint-the-source-too-recommended).

### 7. Audit production logs from Kibana

Tests only cover the paths they run. Export a day of production logs from Kibana or
Elasticsearch and check what services really write, broken down by service:

```bash
logwarden check export.csv --message-field log --ignore-field kubernetes --html report.html
logwarden check export.json --metrics metrics.prom     # per-service numbers for Prometheus
```

It reads Kibana CSV reports, Elasticsearch `_search` responses and NDJSON dumps, and unwraps
records that Fluent Bit wrapped. See
[production logs](https://github.com/Ingeniator/logwarden/blob/main/docs/integration.md#7-production-logs-kibana-and-elasticsearch-exports).

### 8. Keep AI coding agents in line

- **Claude Code:** `/plugin marketplace add Ingeniator/logwarden`, then
  `/plugin install logwarden@logwarden`. This adds a skill for writing good logs, the MCP
  server and `/logwarden:check`.
- **Other agents:** `logwarden init --agent` adds a logwarden section to `AGENTS.md`. The MCP
  server (`uvx --from 'logwarden[mcp]' logwarden mcp`) provides `check_logs`, `explain_rule`,
  `list_rules` and `get_standard`.
- **Machine output:** `logwarden check --json` gives each finding's line, rule and fix hint.

## Quick start

```bash
uv add --dev logwarden                      # or: pipx install logwarden / uvx logwarden
logwarden init --agent                      # fields file + AGENTS.md section
logwarden init --from logs.jsonl            # later: declare the service's own fields, move to the full standard
LOGWARDEN_CAPTURE=logs.jsonl pytest         # capture logs (pytest plugin, auto-registered)
logwarden check logs.jsonl                  # check them
```

Project settings go in `[tool.logwarden]` in `pyproject.toml`, or in `logwarden.toml`.
[docs/integration.md](https://github.com/Ingeniator/logwarden/blob/main/docs/integration.md)
covers capturing logs from any stack, project-only fields, the baseline, the event catalog and
migrating a legacy service.

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
| `adr_event_catalog` | an event missing from the event catalog, when `events` is configured |

**Metrics (experimental, v0.12.0):** `logwarden check --signal metrics` checks a service's
saved `/metrics` output (Prometheus text) against the
[metrics standard](https://github.com/Ingeniator/logwarden/blob/main/docs/metrics-standard.md):
semconv names, units and instruments, the same label rules as for log fields, and
`adr_metric_cardinality` for labels with unbounded values.

The standard, with the reasoning behind each rule, is in
[docs/standard.md](https://github.com/Ingeniator/logwarden/blob/main/docs/standard.md). Each
rule's failing and passing examples and its fix are in
[docs/rules.md](https://github.com/Ingeniator/logwarden/blob/main/docs/rules.md).

## How it compares

| | Checks | When |
|---|---|---|
| Logger linters (Ruff `LOG`/`G`, sloglint) | how the logger is called in source | before running |
| Schema checks in the log pipeline | records arriving in the log store | in production, after the fact |
| **logwarden** | the lines services really write, including their setup, middleware and libraries | in tests and CI, before merge |

These tools complement each other: logwarden's `lint-code` and its recommended Ruff and
sloglint config cover the source side.

## How it works

Each log line becomes a sample for [OTel Weaver](https://github.com/open-telemetry/weaver)
`registry live-check`. Weaver checks the field names against the semconv registry, plus
logwarden's shared fields and the project's `logwarden-fields.yaml`, and runs logwarden's
rules, written in Rego. Checks that span a whole run, such as upstream coverage and the event
catalog, run in Python. `logwarden capture` turns OTLP log records into the same JSON lines
first.

Requirements: Python 3.10+ on Linux, macOS or Windows, or the Docker image (linux/amd64 and
linux/arm64), which works offline. Weaver and the semantic conventions are downloaded on first
use, checked against pinned SHA-256 sums and cached; see
[Without internet access](https://github.com/Ingeniator/logwarden/blob/main/docs/integration.md#without-internet-access). The core package has no dependencies
besides `tomli` on Python 3.10.

**Stability.** Rule ids, the `--json` keys and exit codes are a public interface: they are only
ever added to, never renamed. Every release has notes with a verification table, written
before the work and filled in before the tag
([CHANGELOG.md](https://github.com/Ingeniator/logwarden/blob/main/CHANGELOG.md)).

## Roadmap

Next: a continuous check of the live log stream from Kafka and Fluent Bit, with Prometheus
metrics and findings in Kibana. See [docs/roadmap.md](https://github.com/Ingeniator/logwarden/blob/main/docs/roadmap.md).

## Contributing

Issues and pull requests are welcome. [AGENTS.md](https://github.com/Ingeniator/logwarden/blob/main/AGENTS.md)
describes the layout, the rules for adding a rule and the release process. It is written for
humans and AI agents alike.

## License

[Apache-2.0](https://github.com/Ingeniator/logwarden/blob/main/LICENSE)
