# logwarden Claude Code plugin

Gives Claude Code three things that keep logs useful:

- **Skill `logwarden`**: how to write logs that follow the standard, and how to check the
  logs your code produces before you finish.
- **MCP server `logwarden`**: `check_logs`, `explain_rule`, `list_rules`, `get_standard`.
  It runs through `uvx`, so [uv](https://docs.astral.sh/uv/) must be installed.
- **Command `/logwarden:check`**: captures logs, checks them, and reports findings with fixes.

Install:

```
/plugin marketplace add Ingeniator/logwarden
/plugin install logwarden@logwarden
```

The standard and the rules: https://github.com/Ingeniator/logwarden
