# Connecting a project to logwarden

Four steps: install, capture logs, check in CI, set up agents. Each step is independent,
and most projects need about 10 lines of config.

## 1. Install

```bash
uv add --dev logwarden          # Python projects: CLI + pytest capture plugin
pipx install logwarden          # or a global CLI
uvx logwarden check logs.jsonl  # or no install at all (needs uv)

logwarden init --agent          # logwarden-fields.yaml + a logwarden section in AGENTS.md
```

Weaver, which does the actual checking, is downloaded on first run into the user cache
directory. Set `WEAVER` to use your own binary.

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
`./run-tests 2>&1 | logwarden check -`. Lines that aren't JSON are skipped and counted.

## 3. Check in CI

GitHub Actions:

```yaml
- run: LOGWARDEN_CAPTURE=logs.jsonl uv run pytest
- uses: Ingeniator/logwarden@v0.3.0
  with:
    logs: logs.jsonl
    # fail: "false"     # report only, while a legacy service is being migrated
```

Other CI systems: `uvx logwarden==0.3.0 check logs.jsonl`. The exit code is 0 when the logs
are clean, 1 when there are violations, and 2 when setup failed.

**Migrating an existing service:** start with `fail: "false"`, fix the logging setup
first (it clears `adr_missing_required` on every line at once), then the call sites. Turn
`fail` back on when the count reaches zero.

## 4. Project config and project-only fields

```toml
# pyproject.toml (or the same keys at top level in logwarden.toml)
[tool.logwarden]
fields = "logwarden-fields.yaml"
```

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
