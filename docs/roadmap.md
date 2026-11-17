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
upstreams = "upstreams.yaml"
baseline = ".logwarden-baseline.json"
disable = []            # rule ids; each needs a reason comment
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
- Upstream failure coverage: every host in `upstreams.yaml` has a failure log in the run.

### M4 (v0.6.0): any language
- Docker image for CI without Python.
- Capture over OTLP (Weaver's live-check receiver), for any language with an OTel SDK.
- Setup recipes for Node (pino), Go (slog) and Java (logback), with example output that passes.
- Source-code presets: Ruff config and Semgrep rules (`logwarden lint-code`).

### Later
HTML report, with a diff against the base branch · PR comment with new/fixed counts · event-name catalog.
