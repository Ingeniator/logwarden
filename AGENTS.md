# AGENTS.md: working on logwarden

Instructions for AI agents (and humans) changing this repo. To *use* logwarden in another
project, see `skills/logwarden/SKILL.md` and `docs/integration.md` instead.

## What this repo is

A guardrail for structured logs. `bin/logwarden check` converts JSON log lines into
OTel Weaver samples (`lib/to-weaver.jq`), runs `weaver registry live-check` with the
registry in `registry/` and the rules in `policies/logwarden.rego`, then summarises the
result (`lib/summary.jq`) with the fix hints from `lib/rules.json`.

| Path | Role |
|---|---|
| `bin/logwarden` | CLI (bash). `check`, `rules`, `init`, `setup` |
| `policies/logwarden.rego` | Rules that run per log line (Rego, package `live_check_advice`) |
| `lib/rules.json` | Rule id → section, title, hint, doc anchor. Used by the CLI and MCP |
| `registry/` | Weaver registry: semconv dependency + shared domain fields |
| `docs/standard.md`, `docs/rules.md` | The standard, and one section per rule id |
| `python/logwarden_pytest/` | pytest plugin that captures logs (`LOGWARDEN_CAPTURE`) |
| `mcp/server.py` | MCP server wrapping the CLI |
| `skills/`, `templates/` | Files `logwarden init --agent` copies into other projects |
| `examples/`, `tests/smoke.sh` | Good and bad logs, and the test that uses them |

## Commands

```bash
make test            # smoke test (downloads weaver to .tools/ on first run)
bin/logwarden check examples/bad.jsonl
bin/logwarden check examples/bad.jsonl --json | jq .by_rule
```

`make test` must pass before you commit.

## Adding or changing a rule

Do all of these in one change:
1. Add the rule to `policies/logwarden.rego`. The message starts with the standard section
   (`"§4: ..."`) and says what to use instead.
2. Add an entry to `lib/rules.json` (`section`, `title`, `hint`, `doc`).
3. Add `## <rule_id>` to `docs/rules.md` with a failing line, a passing line and the fix.
   The MCP tool `explain_rule` reads this heading, so the format matters.
4. Mark the rule **[checked]** in `docs/standard.md`.
5. Add a failing line to `examples/bad.jsonl`, and the rule id to the loop in
   `tests/smoke.sh`. Keep `examples/good.jsonl` passing.
6. Add a row to the rule table in `README.md`.

Rego runs per sample (one log line or one attribute), so it can't check anything across
a run, such as "never seen". Write those as jq in `lib/` and call them from `cmd_check`.

## Constraints

- The CLI needs only `bash` (3.2+, macOS default), `jq`, `curl`, `tar`. Don't add a
  Python or Node dependency to it.
- Rule ids are a public interface: CI configs, agent prompts and `--json` consumers use
  them. Don't rename one. Add a new id and keep the old one if needed.
- `--json` output fields are a public interface too. Only add fields.
- Weaver 0.28 emits findings as `{id, level, message}`. `summary.jq` also accepts the
  older `advice_type`/`advice_level` names.

## Releasing

1. Bump the version in `bin/logwarden` (`VERSION`) and `pyproject.toml`.
2. Update the `@vX.Y.Z` references in `README.md` and `docs/integration.md`.
3. Commit, then `git tag -a vX.Y.Z -m "..." && git push origin main vX.Y.Z`.
