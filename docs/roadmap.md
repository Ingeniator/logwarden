# Roadmap

## Decisions (2026-11-17)

| Topic | Decision |
|---|---|
| Scope | Any language and any team. Python services are supported first. |
| Purpose | Check logs **and** help services comply: ship a logging-setup library as well as the checker. |
| Distribution | Public PyPI package `logwarden` is the main channel. A git tag is the single version for every channel. |
| Standard | `docs/standard.md` here is canonical. Project ADRs (e.g. ai-suite ADR-0001) link to it and only add project-specific notes. |
| Agents | Claude Code plugin from this repo's own marketplace, with versioned updates. Other agents get the `AGENTS.md` snippet. |

## Distribution

```
                git tag vX.Y.Z
                      │
  ┌───────────────────┼────────────────────┬──────────────────────┐
  ▼                   ▼                    ▼                      ▼
PyPI: logwarden    GitHub Action        Docker image          Claude Code plugin
CLI, pytest        uvx logwarden        weaver + logwarden    skill, MCP (uvx),
plugin, MCP,       ==X.Y.Z check        for non-Python CI     /logwarden:check
rules, docs,
logging lib
```

Package layout (extras keep the base install small):

| Install | Gives |
|---|---|
| `logwarden` | CLI, pytest plugin, rules, registry, docs. Weaver downloads on first run |
| `logwarden[mcp]` | `logwarden mcp` server |
| `logwarden[structlog]` | `logwarden.logging.configure()` and the test helpers |

## How a project connects

```toml
# pyproject.toml, or logwarden.toml in projects without Python
[tool.logwarden]
fields = "logwarden-fields.yaml"
upstreams = ["api.openai.com"]  # hosts whose failure path must be tested
baseline = ".logwarden-baseline.json"
disable = { adr_non_json_line = "reason" }   # each disabled rule needs a reason
```

1. `uv add --dev logwarden`, or `uvx logwarden` with no install
2. `logwarden init` writes the config and fields file
3. CI: `uses: Ingeniator/logwarden@vX.Y.Z`
4. Agents: `/plugin marketplace add Ingeniator/logwarden`, then `/plugin install logwarden`

## Milestones

Each milestone's release notes are written in `docs/releases/` before work starts, and
verified claim by claim before the tag (see "Releasing" in `AGENTS.md`).

### M1 (v0.3.0): one package, every channel
- Port the CLI from bash + jq to Python, with the same commands, `--json` fields and exit codes.
  Rules stay in Rego and run in Weaver.
- Ship the rules, registry, docs and templates as package data. Read `[tool.logwarden]` / `logwarden.toml`.
- pytest plugin and MCP server (`logwarden mcp`) move into the package.
- The GitHub Action runs `uvx logwarden==X.Y.Z`. PyPI release uses trusted publishing from a tag.
- Claude Code plugin + `marketplace.json`, with the skill, MCP config and `/logwarden:check`.
- ai-suite ADR-0001 points to `docs/standard.md`.

### M2 (v0.4.0): safe to adopt on existing services
- Baseline: `logwarden baseline` records the current findings, and `check` fails only on new ones.
- Sensitive-data rule (§8): sensitive key names plus value patterns (JWT, API-key prefixes,
  emails, card numbers).
- Non-JSON line rule (§1), and a trace-id/span-id format rule.
- `disable` in config, with a reason required.

### M3 (v0.5.0): make good logs the easy path
- `logwarden.logging.configure(service_name=..., ...)`, the reference structlog setup
  (required fields, trace context, exceptions → semconv, redaction, noise control).
- Test helpers: `assert_failure_logged`, `extract_error_code`, `extract_error_message`, `scrub`.
- Upstream failure coverage: every host in `upstreams` has a failure log in the run.

### M4 (v0.6.0): any language (done)
- Docker image for CI without Python: `ghcr.io/ingeniator/logwarden`.
- Capture over OTLP for any language with an OTel SDK: `logwarden capture`, logwarden's own
  OTLP/HTTP receiver (Weaver's receiver returns no lines, so baseline and upstream coverage
  wouldn't work with it).
- Setup recipes for Node (pino), Go (slog) and Java (logback), with example output that passes.
- Source checks: Semgrep rules and `logwarden lint-code`. (The Ruff and sloglint config has
  been recommended in `docs/integration.md` §6 since 2026-11-18.)

### M5 (v0.7.0): review what a change does to the logs (done)
- Snapshots and `check --compare` against the base branch: new and fixed findings, new event names.
- Self-contained HTML report (`--html`) and Markdown summary (`--markdown`).
- GitHub Action: base snapshot through the Actions cache, one PR comment updated on each push, report artifact.
- Event catalog: `logwarden catalog`, a committed `logwarden-events.toml`, and the rule
  `adr_event_catalog` when it is configured.

### M6 (v0.8.0): findings on the source line; analyse Kibana exports (done)
- SARIF from `check` (callsite fields from `configure(callsite=True)`) and `lint-code`; the
  Action uploads it to code scanning.
- `check` reads Kibana CSV reports, Elasticsearch `_search` responses and NDJSON dumps.
- `[source]` unwraps records that Fluent Bit wrapped (`log` string, `Merge_Log`, metadata).
- Per-service metrics: a breakdown in every output, and `--metrics` in Prometheus text format.
- pre-commit hook for `lint-code`.

### M7 (v0.9.0): a first useful result in 10 minutes, without code changes (done)
- Rule profiles: `essential` (sensitive data, non-JSON lines, required fields) to start with,
  `standard` (every rule) as the default.
- Ranked fixes: findings grouped by source line or logger and event, "Fix first" in every report.
- Scheduled check of the logs already in Elasticsearch (GitLab template job or cron), compared
  with the previous run, metrics to a Pushgateway, a Grafana dashboard.
- Four "Start here" pages (Elasticsearch, GitLab, GitHub, AI agents); everything else under
  "Advanced". `STABILITY.md`: the public interface, how it changes, the conditions for 1.0.
- Decided 2026-11-22: no long-running `watch` service. The team using logwarden runs it in CI
  only, with no shared state, and an hourly job gives the same view with nothing to operate.

### M8 (v0.10.0): from `essential` to the full standard in one reviewed change (done)
- `init --from LOGS` writes the fields file from real logs, declaring custom fields but not
  synonyms or sensitive-looking fields; the summary shows how big the move to `standard` is.
- Weekly workflow that opens a PR for new Weaver and semconv pins.

### M9 (v0.11.0): which log calls do the tests run?
- Outcome: `logwarden log-coverage` shows, per file and level, the log calls that no test runs
  (from coverage.py data) or that never wrote a line (from captured logs), with an opt-in CI
  gate such as "every error path is tested". Python first.

### M10 (v0.12.0): does checking metrics pay off?
- Outcome: a go/no-go decision. An experimental `check --signal metrics` on saved Prometheus
  text: Weaver's findings with hints, the label rules shared with logs, and cardinality
  (unbounded labels, series budget), one finding per metric and label.
- Trial on the `/metrics` output of five real services, compared with `promtool` and Weaver
  alone, with a written decision rule (see the [v0.12.0 notes](releases/v0.12.0.md)).
- **Decision (2026-11-24): go.** In 4 of 5 services logwarden found problems worth fixing that
  neither `promtool` nor Weaver alone reports usefully; 1 of 109 findings was not worth fixing.

### M11 (v0.13.0): metrics in the everyday workflow
- Outcome: metrics checked with one option in the same CI, merge request and reports as logs.
- [Metrics standard](metrics-standard.md) rules for names, resources and required metrics;
  OTLP input and `capture /v1/metrics`; domain metrics; baseline, compare, SARIF, Code Quality,
  the GitLab template, the Action and MCP. Scope cut to what the trial found useful.

### 1.0
- When at least two teams have used logwarden for a month with no change to the public
  interface needed (see `STABILITY.md` from v0.9.0).

### Later
- `logwarden watch`: a long-running check of the live stream (Kafka, Fluent Bit http,
  OTLP), if a user needs findings within minutes rather than an hour.
- `lint-code` rules for metric names and labels in instrument-creation calls.
- Consistency across signals: the same attribute names in a service's logs and metrics.
