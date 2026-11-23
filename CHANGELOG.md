# Changelog

Full release notes, each with a verification table, are in [docs/releases/](docs/releases/).

| Version | Status | Summary |
|---|---|---|
| [v0.13.0](docs/releases/v0.13.0.md) | Planned, conditional | Metrics in the everyday workflow: OTLP and capture, naming, resource and required-metric rules, baseline, merge requests, Action, MCP. Only if v0.12.0 says go |
| [v0.12.0](docs/releases/v0.12.0.md) | Planned | Experiment: `check --signal metrics` on Prometheus text with cardinality and label rules, tried on five real services for a go/no-go decision |
| [v0.11.0](docs/releases/v0.11.0.md) | Released 2026-11-23 | `logwarden log-coverage`: which log calls the tests run (from coverage data) or that wrote a line (from captured logs), per file and level, with an opt-in CI gate |
| [v0.10.3](docs/releases/v0.10.3.md) | Released 2026-11-23 | The semantic conventions + GenAI registry is prepared once in the cache instead of copied on every check (about 0.1 s per check) |
| [v0.10.2](docs/releases/v0.10.2.md) | Released 2026-11-23 | Bug fixes: list fields pass as `string[]` etc.; `gen_ai.*` fields pass (GenAI semantic conventions bundled); source lines with logging set up in `conftest.py`; `init --from` naming hint follows the standard; timestamp ids no longer reported as card numbers; a baselined secret kind no longer hides another kind in the field (old secret entries must be regenerated); Python 3.10/3.11 unpack the GenAI archive |
| [v0.10.1](docs/releases/v0.10.1.md) | Released 2026-11-22 | logwarden passes its own standard (JSON messages), faster checks of large files |
| [v0.10.0](docs/releases/v0.10.0.md) | Released 2026-11-22 | From `essential` to the full standard: `init --from` writes the fields file from real logs; pin updates |
| [v0.9.0](docs/releases/v0.9.0.md) | Released 2026-11-22 | First useful result in 10 minutes: `essential` profile, ranked fixes, scheduled check of Elasticsearch logs, four Start-here paths, `STABILITY.md` |
| [v0.8.1](docs/releases/v0.8.1.md) | Released 2026-11-22 | GitLab: Code Quality report in merge requests, CI template; offline setup docs |
| [v0.8.0](docs/releases/v0.8.0.md) | Released 2026-11-20 | SARIF on the source line, Kibana and Elasticsearch exports as input, Fluent Bit `[source]`, metrics per service, pre-commit hook |
| [v0.7.0](docs/releases/v0.7.0.md) | Released 2026-11-19 | Compare with the base branch, HTML report, PR comment, event catalog and `adr_event_catalog` |
| [v0.6.0](docs/releases/v0.6.0.md) | Released 2026-11-19 | Any language: OTLP capture, Docker image, Node/Go/Java recipes, `lint-code` with Semgrep |
| [v0.5.0](docs/releases/v0.5.0.md) | Released 2026-11-18 | `logwarden.logging.configure()`, error and test helpers, upstream failure coverage |
| [v0.4.0](docs/releases/v0.4.0.md) | Released 2026-11-18 | Baseline, `disable` with reason, sensitive-data / non-JSON / trace-context rules |
| [v0.3.0](docs/releases/v0.3.0.md) | Released 2026-11-18 | One PyPI package for every channel: Python CLI, pytest plugin, MCP, Claude Code plugin |
| [v0.2.0](docs/releases/v0.2.0.md) | Released 2026-11-17 | Project fields file, agent-friendly findings, pytest plugin, MCP server, docs and skill |
| [v0.1.0](docs/releases/v0.1.0.md) | Released 2026-11-17 | First check of JSON logs against the logging standard on OTel Weaver |
