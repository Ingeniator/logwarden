# Start here: AI coding agents

Teach your coding agent the logging standard, and let it check the logs its code writes.
About 5 minutes.

## Claude Code

In Claude Code:

```text
/plugin marketplace add Ingeniator/logwarden
/plugin install logwarden@logwarden
```

Restart Claude Code. The plugin adds:

- a skill, used whenever the agent writes code that logs,
- the MCP server, with `check_logs`, `explain_rule`, `list_rules` and `get_standard`,
- `/logwarden:check`, which captures the project's logs and checks them.

Then ask for a first result:

```text
/logwarden:check
```

To start with the `essential` rules (secrets and personal data, non-JSON lines, required
service fields), add `logwarden.toml` with `profile = "essential"` to the project first. The
agent's checks then use it too.

## Other agents (Codex, Cursor, Copilot, …)

```bash
uvx logwarden init --agent
```

This adds a logwarden section to `AGENTS.md` with the rules and the commands to check. For
agents that support MCP, add the server:

```json
{"mcpServers": {"logwarden": {"command": "uvx", "args": ["--from", "logwarden[mcp]==0.10.2", "logwarden", "mcp"]}}}
```

## Next

- When `essential` is clean, ask the agent to move the project to the full standard: it runs
  `logwarden init --from` on the captured logs and fixes the fields it lists.

- Run the check in CI too, so the agent's work is checked like everyone else's:
  [GitLab](gitlab.md) or [GitHub](github.md).
- More: the [integration guide](../integration.md#5-ai-agents).
