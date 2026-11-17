# logwarden

A guardrail for logs. logwarden reads the JSON logs your service actually writes and flags
every line that would make debugging harder: ad-hoc field names, missing service info,
free-text events, and errors that don't say what failed. It exits with code 1 on
problems, so CI and AI agents can use it the way they use tests.

It checks output, not source code, so it works for any language. Field names follow
[OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/),
the names traces and metrics already use, so logs, traces and metrics can be queried together.

```
$ logwarden check logs.jsonl
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

The standard is in [docs/standard.md](docs/standard.md), and each rule's fix is in
[docs/rules.md](docs/rules.md).

## Use it in a project

```bash
logwarden init --agent                       # fields file + agent instructions
LOGWARDEN_CAPTURE=logs.jsonl pytest          # capture logs (pytest plugin)
logwarden check logs.jsonl                   # check them
```

```yaml
# CI
- uses: Ingeniator/logwarden@v0.2.0
  with:
    logs: logs.jsonl
```

See [docs/integration.md](docs/integration.md) for installation, capturing logs from any
stack, project-only fields and migrating a legacy service.

## For AI agents

- **Agents using logwarden in another project:** read
  [skills/logwarden/SKILL.md](skills/logwarden/SKILL.md). `logwarden init --agent`
  installs it, along with an `AGENTS.md` section.
- **MCP:** `uv run --script mcp/server.py` provides `check_logs`, `explain_rule`,
  `list_rules` and `get_standard`.
- **Machine output:** `logwarden check --json` gives each finding's line number, rule
  and fix hint.
- **Agents working on this repo:** read [AGENTS.md](AGENTS.md).

## How it works

Each log line becomes a sample for [OTel Weaver](https://github.com/open-telemetry/weaver)
`registry live-check`. Weaver checks the sample's field names against the semconv
registry (plus `registry/` and the project's `logwarden-fields.yaml`) and runs the rules
in `policies/logwarden.rego`. Requirements are `bash`, `jq` and `curl`. Weaver is
downloaded on first use.

## Not covered yet

- Checks across the whole run, such as "every upstream has a logged failure test".
- Failing on non-JSON lines (they're counted as skipped for now).
- `logwarden report`, an HTML report.
- Source-code rules (Ruff, Semgrep) are separate. See the standard's enforcement notes.
