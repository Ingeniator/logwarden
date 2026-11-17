# Connecting a project to logwarden

Four steps: install, capture logs, check in CI, set up agents. Each step is independent,
and most projects need about 10 lines of config.

## 1. Install

```bash
# CLI (bash + jq; downloads OTel Weaver on first run)
git clone --depth 1 --branch v0.2.0 https://github.com/Ingeniator/logwarden ~/.logwarden
export PATH="$HOME/.logwarden/bin:$PATH"

# In your project: create logwarden-fields.yaml (and agent files with --agent)
logwarden init --agent
```

## 2. Capture the logs your code produces

logwarden checks real output, so the project needs a run that produces logs: tests,
or a smoke run. Include failure cases (500s, timeouts). §10 of the standard is
about exactly those lines.

**Python / pytest.** Install the capture plugin as a dev dependency:

```bash
uv add --dev "logwarden-pytest @ git+https://github.com/Ingeniator/logwarden@v0.2.0"
# or: pip install "logwarden-pytest @ git+https://github.com/Ingeniator/logwarden@v0.2.0"

LOGWARDEN_CAPTURE=logs.jsonl pytest
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
- run: LOGWARDEN_CAPTURE=logs.jsonl pytest
- uses: Ingeniator/logwarden@v0.2.0
  with:
    logs: logs.jsonl
    # fail: "false"     # report only, while a legacy service is being migrated
```

Other CI systems: clone the repo at the tag and run `bin/logwarden check logs.jsonl`.
The exit code is 0 when the logs are clean, 1 when there are violations, and 2 when
setup failed.

**Migrating an existing service:** start with `fail: "false"`, fix the logging setup
first (it clears `adr_missing_required` on every line at once), then the call sites. Turn
`fail` back on when the count reaches zero.

## 4. Project-only fields

`logwarden-fields.yaml` in the project root (found automatically) lists fields only this
service logs. The template that `logwarden init` creates explains the format and when a
field belongs there. Fields that several services share go into logwarden's
`registry/logwarden.yaml` instead, through a PR to this repo.

## 5. AI agents

Agents should check the logs of the code they write, the same way they run tests.

| Agent | Setup |
|---|---|
| Claude Code | `logwarden init --agent` installs `.claude/skills/logwarden/SKILL.md`. Optionally add MCP: `claude mcp add logwarden -- uv run --script ~/.logwarden/mcp/server.py` |
| Other agents (Codex, Cursor, …) | `logwarden init --agent` also adds a logwarden section to `AGENTS.md`. Agents with MCP support can use the same server command |

MCP tools: `check_logs(path | lines)`, `explain_rule(rule_id)`, `list_rules()`,
`get_standard()`. Agents with shell access can use the CLI with `--json` instead. Both
give the same result.

The skill tells the agent to fix findings in its own changes, never silence them,
and not to rewrite unrelated legacy logging unless asked.
