# Stability

What you can build on, how it changes, and when logwarden becomes 1.0.

logwarden is at 0.x, but the interfaces below are already treated as stable: CI configs, agent
prompts, dashboards and other tools depend on them.

## Public interface

| Interface | What is stable |
|---|---|
| **Rule ids** | The ids below. A rule may get stricter or find more, but an id never changes meaning. |
| **Exit codes** | `0` no violations, `1` violations, `2` usage or setup error, for every command. |
| **CLI** | Command names and options, and what they write (`--json`, `--snapshot`, `--sarif`, `--codequality`, `--metrics`, `--markdown`, `--html`). With `LOGWARDEN_LOG_FORMAT=json`, the fields and `event` names of logwarden's own records (new events may be added). |
| **`--json` keys** | The keys of the result and of each finding, rule and service entry. |
| **Config** | `[tool.logwarden]` / `logwarden.toml` keys: `fields`, `baseline`, `disable`, `upstreams`, `events`, `profile`, `[source]` (`format`, `message_field`, `ignore_fields`, `service_field`); the fields file format. |
| **Baseline and snapshot files** | `format: 1`. A baseline written by an older version keeps working. |
| **Profiles** | `essential` stays `adr_sensitive_data`, `adr_non_json_line` and `adr_missing_required`; `standard` is every rule. |
| **Metrics** | `logwarden_lines_total`, `logwarden_lines_with_violations_total`, `logwarden_violations_total` and their labels (`service`, `rule`). |
| **Environment variables** | `LOGWARDEN_CAPTURE`, `LOGWARDEN_FIELDS`, `LOGWARDEN_LOG_FORMAT`, `LOGWARDEN_CACHE`, `LOGWARDEN_SEMCONV`, `LOGWARDEN_SEMCONV_GENAI`, `WEAVER`, `LOGWARDEN_ES_AUTH`, `LOGWARDEN_ES_CA`, `LOGWARDEN_PUSHGATEWAY_AUTH`, and those read by `configure()` (`LOGWARDEN_LEVEL`, `LOGWARDEN_CALLSITE`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`, `SERVICE_VERSION`, `DEPLOYMENT_ENVIRONMENT`). |
| **CI integrations** | GitHub Action inputs and outputs; GitLab template job names (`.logwarden`, `.logwarden-production`) and their `LOGWARDEN_*` variables. |
| **Python API** | `logwarden.logging.configure()` and the helpers it exports, `logwarden.testing`, the pytest plugin. |
| **MCP** | Tool names (`check_logs`, `explain_rule`, `list_rules`, `get_standard`) and their parameters. |
| **Docker image tags** | A published `:X.Y.Z` tag is never overwritten. `:latest` moves with each release. |

Rule ids:

<!-- rule-ids:begin -->
`adr_missing_required`, `adr_event_name`, `adr_event_catalog`, `adr_banned_synonym`,
`adr_semconv_prefix`, `adr_environment_value`, `adr_failure_context`, `adr_sensitive_data`,
`adr_non_json_line`, `adr_trace_context`, `adr_upstream_failure_coverage`, `missing_attribute`,
`deprecated`, `type_mismatch`
<!-- rule-ids:end -->

**Not part of it:** the wording of messages and hints, the layout of the text output and of the
Markdown and HTML reports, how "Fix first" groups findings, and anything marked
*experimental* in the docs. These improve between versions.

**Findings can change without a code change.** Field names, types and deprecations come from the
OpenTelemetry semantic conventions. A newer semconv pin can report a field as deprecated that
wasn't before. Every pin update is in the release notes.

## How it changes

- **Additions** (a rule, an option, a `--json` key, a config key) come in any minor version.
  New rules that would fail most existing services are added outside the `essential` profile.
- **Removals and renames** are announced in the release notes, warned about at run time for at
  least two minor versions, and happen only in a major version. Until then the old name keeps
  working next to the new one.
- **Fixes** go into the latest minor version (`0.9.x`, then `0.10.x`, …). Upgrading within a
  minor version never needs a config change.
- Every release has notes in [docs/releases](https://github.com/Ingeniator/logwarden/tree/main/docs/releases) (listed in [CHANGELOG.md](https://github.com/Ingeniator/logwarden/blob/main/CHANGELOG.md)) written before the work and
  verified claim by claim before the tag.

## 1.0

logwarden becomes 1.0 when at least two teams have used it for a month and no change to the
public interface above was needed in that time. From 1.0, the rules above apply with semantic
versioning: removals only in 2.0.
