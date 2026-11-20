<!-- logwarden-report -->
### logwarden: ❌ 10 violations
**3** records · **10** violations · 1 blank or non-JSON lines

**Compared with the base `base.json`:** **10 new** · 0 fixed · 0 unchanged

New events: `Loaded 3 models`, `user_logged_in` · No longer logged: `billing_sync_complete`

#### New findings

| Line | Rule | Event | Message |
|---|---|---|---|
| 1 | `adr_banned_synonym` | `Loaded 3 models` | §2: 'status' is a banned synonym; use http.response.status_code |
| 1 | `adr_semconv_prefix` | `Loaded 3 models` | §2: 'http_route' uses '_' where semconv uses '.'; probably 'http.route' |
| 1 | `missing_attribute` | `Loaded 3 models` | Attribute 'foo_bar' does not exist in the registry. |
| 1 | `adr_environment_value` | `Loaded 3 models` | §3: environment 'dev' must be one of development, staging, test, production |
| 1 | `adr_missing_required` | `Loaded 3 models` | §3: required field 'service.version' missing |
| 1 | `adr_event_name` | `Loaded 3 models` | §4: event 'Loaded 3 models' must be a constant snake_case name |
| 2 | `adr_non_json_line` |  | §1: line is not a JSON object (plain text) |
| 3 | `adr_failure_context` | `upstream_call_failed` | §10: 'upstream_call_failed' is a failure line without exception.type, error.type or http.response.status_code |
| 4 | `adr_sensitive_data` | `user_logged_in` | §8: 'password' holds a secret or credential; don't log it (log an id or a redacted value) |
| 4 | `adr_trace_context` | `user_logged_in` | §7: trace_id must be 32 lowercase hex characters (W3C), not all zeros |

#### Violations by rule

| Count | Rule | Fix |
|---|---|---|
| 1 | [`adr_banned_synonym`](https://github.com/Ingeniator/logwarden/blob/v0.7.0/docs/rules.md#adr_banned_synonym) Banned synonym for an OTel semconv field | Rename the field to the semconv name given in the message. |
| 1 | [`adr_environment_value`](https://github.com/Ingeniator/logwarden/blob/v0.7.0/docs/rules.md#adr_environment_value) Environment value is not a documented one | Set deployment.environment.name to development, staging, test or production. |
| 1 | [`adr_event_name`](https://github.com/Ingeniator/logwarden/blob/v0.7.0/docs/rules.md#adr_event_name) Event is a constant snake_case name | Use a fixed name like import_completed and move variable data into fields: log.info("import_completed", rows_imported=n). |
| 1 | [`adr_failure_context`](https://github.com/Ingeniator/logwarden/blob/v0.7.0/docs/rules.md#adr_failure_context) Failure line does not say what failed | On warning/error and *_failed lines log exc_info=True (gives exception.type), or error.type, or http.response.status_code. |
| 1 | [`adr_missing_required`](https://github.com/Ingeniator/logwarden/blob/v0.7.0/docs/rules.md#adr_missing_required) Required resource fields on every line | Add service.name, service.version and deployment.environment.name in one shared processor/formatter, not at call sites. |
| 1 | [`adr_non_json_line`](https://github.com/Ingeniator/logwarden/blob/v0.7.0/docs/rules.md#adr_non_json_line) Line is not a JSON object | Route every logger (including third-party libraries) through the JSON formatter. If the input mixes in non-log output, capture only the service's output. |
| 1 | [`adr_semconv_prefix`](https://github.com/Ingeniator/logwarden/blob/v0.7.0/docs/rules.md#adr_semconv_prefix) Semconv name written with underscores | Use the dotted semconv key (http_route -&gt; http.route). In structlog pass it as **{"http.route": value}. |
| 1 | [`adr_sensitive_data`](https://github.com/Ingeniator/logwarden/blob/v0.7.0/docs/rules.md#adr_sensitive_data) Secret or personal data in a log line | Don't log secrets, tokens, credentials, emails or card numbers. Log an id, a size or a redacted value instead, and redact in the shared processor. |
| 1 | [`adr_trace_context`](https://github.com/Ingeniator/logwarden/blob/v0.7.0/docs/rules.md#adr_trace_context) trace_id/span_id not in W3C format | Take trace_id and span_id from the active OTel span: format(ctx.trace_id, "032x") and format(ctx.span_id, "016x"); omit both when no span is active. |
| 1 | [`missing_attribute`](https://github.com/Ingeniator/logwarden/blob/v0.7.0/docs/rules.md#missing_attribute) Unknown field | Use the OTel semconv name if one exists. If it is a real domain field, declare it in logwarden-fields.yaml. |

#### By service

| Service | Lines | Violations | Compliant lines | Top rule |
|---|---|---|---|---|
| `yallmp` | 3 | 9 | 0.0% | `adr_banned_synonym` |
| `(none)` | 1 | 1 | 0.0% | `adr_non_json_line` |

<details><summary>Findings (first 10 of 10)</summary>

| Line | Rule | Message |
|---|---|---|
| 1 | `adr_banned_synonym` | §2: 'status' is a banned synonym; use http.response.status_code |
| 1 | `adr_semconv_prefix` | §2: 'http_route' uses '_' where semconv uses '.'; probably 'http.route' |
| 1 | `missing_attribute` | Attribute 'foo_bar' does not exist in the registry. |
| 1 | `adr_environment_value` | §3: environment 'dev' must be one of development, staging, test, production |
| 1 | `adr_missing_required` | §3: required field 'service.version' missing |
| 1 | `adr_event_name` | §4: event 'Loaded 3 models' must be a constant snake_case name |
| 2 | `adr_non_json_line` | §1: line is not a JSON object (plain text) |
| 3 | `adr_failure_context` | §10: 'upstream_call_failed' is a failure line without exception.type, error.type or http.response.status_code |
| 4 | `adr_sensitive_data` | §8: 'password' holds a secret or credential; don't log it (log an id or a redacted value) |
| 4 | `adr_trace_context` | §7: trace_id must be 32 lowercase hex characters (W3C), not all zeros |

</details>

Not failing: `undefined_enum_variant` 1

<sub>logwarden 0.7.0</sub>
