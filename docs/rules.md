# Rules

Every rule logwarden reports, with a failing line and the fix. `logwarden rules <id>`
prints the short version. The standard behind them is in [standard.md](standard.md).

## adr_missing_required

A line has no `service.name`, `service.version` or `deployment.environment.name`.

```json
{"level":"info","event":"started","logger":"app"}                                   ❌
{"level":"info","event":"started","logger":"app","service.name":"billing-api",
 "service.version":"1.4.2","deployment.environment.name":"production"}             ✅
```

**Fix:** add these fields in one shared processor or formatter that every log line passes
through, including third-party loggers. Don't add them at call sites. If only some lines
fail, those lines bypass the shared formatter, usually because a library logger isn't
routed through it.

## adr_event_name

`event` isn't a constant snake_case name.

```python
log.info(f"Loaded {n} models")              # ❌ event = "Loaded 3 models"
log.info("models_loaded", models_count=n)   # ✅
```

**Fix:** pick a fixed name in past tense or as a noun phrase, and move the variable parts into
fields. Lines from third-party libraries often fail this too. Map their message into a
field (e.g. `message`) and set a fixed `event` in the shared formatter, or silence the logger.

## adr_event_catalog

Runs only when the config names an event catalog: `events = "logwarden-events.toml"`. Fails
on a line whose event isn't listed in the catalog. Dashboards, alerts and queries match on
event names, so a renamed or new event should be a reviewed change, not a surprise (§4).
Events that `adr_event_name` already rejects aren't reported again.

```toml
# logwarden-events.toml
[events.order_imported]
description = "An order file was parsed and stored."
levels = ["info"]
```

```json
{"event":"order_imported", ...}     ✅ listed
{"event":"order_imprted", ...}      ❌ not in logwarden-events.toml
```

**Fix:** if the event is new on purpose, add it with a description:
`logwarden catalog logs.jsonl -o logwarden-events.toml` adds every new event of a run and
keeps the existing entries. If it's a typo or a renamed event, log the listed name instead.

## adr_banned_synonym

A field uses a common ad-hoc name for something semconv already names. The message says
which name to use.

| Banned | Use |
|---|---|
| `status`, `status_code` | `http.response.status_code` |
| `path` | `url.path` or `http.route` |
| `method` | `http.request.method` |
| `url` | `url.full` |
| `host` | `server.address` |
| `model` | `gen_ai.request.model` |
| `error`, `err`, `exception`, `traceback` | log with `exc_info=True`, or `error.type` |
| `uid` | `user.id` |
| `env` | `deployment.environment.name` |
| `headers` | specific safe fields; never whole headers |

**Fix:** rename the field. In structlog, dotted names are passed as `**{"http.route": route}`.

## adr_semconv_prefix

A field looks like a semconv name written with underscores, like `http_route` or
`db_system_name`.

**Fix:** use the dotted name the message suggests (`http.route`). If it's really a
domain field, rename it so it doesn't start with a semconv namespace.

## adr_environment_value

`deployment.environment.name` isn't `development`, `staging`, `test` or `production`.

**Fix:** map your config values (`dev`, `prod`, `local`) to these four names in one place.

## adr_failure_context

A `warning`/`error` line, or an event ending in `_failed`, doesn't say what failed. It has
none of `exception.type`, `error.type` or `http.response.status_code`.

```python
except Exception:
    log.error("sync_failed")                          # ❌
except Exception:
    log.error("sync_failed", exc_info=True)           # ✅ adds exception.type
log.warning("quota_low", **{"error.type": "quota"})   # ✅ no exception involved
```

**Fix:** pass `exc_info=True` inside `except`, or set `error.type` to a short stable code.
For HTTP failures, add `http.response.status_code`. If the line isn't a failure, lower its
level.

Lines with the event `library_log` (third-party library output, as written by
`logwarden.logging.configure()`) are exempt: the service can't add context to them.

## adr_sensitive_data

A field holds a secret or personal data (§8). Two checks:

- **Field name:** the last part of the key is `password`, `passwd`, `secret`, `api_key`,
  `apikey`, `access_key`, `private_key`, `authorization`, `cookie`, `set_cookie`,
  `credential(s)` or `token` (`csrf_token`, `auth.token`). Also those names as keys inside a
  nested JSON value. Not flagged when the value is redacted (`""`, `***`, `[REDACTED]`,
  `<redacted>`, `[filtered]`, `[masked]`, `xxx`) or a boolean. Token *counts* such as
  `gen_ai.usage.input_tokens` or `token_count` don't match.
- **Value:** any field whose value contains a JWT, a `Bearer` token, an API key in a known
  format (`sk-…`, `ghp_…`, `github_pat_…`, `glpat-…`, `AKIA…`, `xox?-…`, `AIza…`), a
  private-key block, credentials in a URL (`scheme://user:pass@`), an email address, or a
  card number: a card issuer's prefix and length (Visa, Mastercard, American Express,
  Diners Club, Discover, JCB, UnionPay, Maestro), written without separators or in card
  groups (`4111 1111 1111 1111`, `3782 822463 10005`), and passing the Luhn checksum. Ids and
  timestamps such as `20260911-120000` aren't card numbers.

The message names the field and the kind of data, never the value, so the finding is safe
to print.

```python
log.info("user_logged_in", password=password)                 # ❌
log.info("user_logged_in", **{"user.id": user.id})            # ✅
log.warning("db_connect_failed", dsn=settings.database_url)   # ❌ postgres://app:pw@db
log.warning("db_connect_failed", **{"server.address": host})  # ✅
```

**Fix:** remove the value or log an id, a size or a redacted value instead. Redact in the
shared logging processor so no call site can leak it. When a field like `user.email` is
genuinely required, turn the rule off with a reason:
`disable = { adr_sensitive_data = "..." }`.

## adr_non_json_line

A line in the log output isn't a JSON object (§1): plain text such as a library's startup
banner or a printed traceback, or JSON that isn't an object (an array, a number). Blank
lines are ignored. The message gives the kind of line, never its text.

**Fix:** route every logger, including third-party ones (uvicorn, httpx, …), through the
JSON formatter, and don't `print()`. If the input mixes in output that isn't the service's
logs (test runner output, shell noise), capture only the service's output instead.

## adr_trace_context

`trace_id` or `span_id` isn't in the W3C format (§7): `trace_id` must be 32 and `span_id` 16
lowercase hex characters, not all zeros. A `span_id` without a `trace_id` also fails.

```python
event_dict["trace_id"] = str(ctx.trace_id)              # ❌ decimal number
event_dict["trace_id"] = format(ctx.trace_id, "032x")   # ✅
```

**Fix:** take both from the active OTel span (see the reference setup in the standard) and
leave them out when no span is active, instead of logging zeros.

## adr_upstream_failure_coverage

Runs only when the config lists upstreams: `upstreams = ["api.openai.com", ...]`. Fails when
the run has no failure line for an upstream: no `warning`/`error` line (or `*_failed` event)
with `server.address` equal to that host. Then no test exercised what happens when that
upstream fails, and nobody knows whether the failure log is any use (§10). The finding is
about the whole run, so its `line` is `null` (`run` in text output).

**Fix:** add a test where a call to the upstream fails (timeout, 429, 5xx) and the code logs
it with `server.address`, `http.response.status_code` or `error.type` (see §10). Remove hosts
the service no longer calls. `logwarden.testing.assert_failure_logged` checks the line in
the test itself.

## missing_attribute

The field isn't in OTel semconv, isn't one of logwarden's shared fields, and isn't in the
project's `logwarden-fields.yaml`.

**Fix**, in this order:
1. A semconv name exists for it, so use that. Search the
   [attribute registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/).
2. It's a real domain field (safe, snake_case, no semconv prefix), so declare it in
   `logwarden-fields.yaml`.
3. It's noise, or it's sensitive (`payload`, `body`, `prompt`), so stop logging it.

Don't declare a field just to make the check pass. The fields file is the list of what
the service is allowed to log.

## deprecated

A semconv field has been renamed. The message gives the replacement.

**Fix:** rename it. Example: `gen_ai.system` → `gen_ai.provider.name`.

## type_mismatch

A field has a different type from the one semconv declares, e.g. `http.response.status_code` as
`"200"`.

**Fix:** log the declared type.

## Not failing

- `undefined_enum_variant` (information): a value isn't in a semconv enum. It's shown as a
  count and doesn't fail the check.
- `skipped_lines` counts the lines that weren't checked as records: blank lines and the
  lines reported by `adr_non_json_line`.

## Baseline and disabled rules

Not rules, but they change what fails:

- **Baseline:** `logwarden baseline logs.jsonl` records the current findings in
  `.logwarden-baseline.json`. With `baseline = ".logwarden-baseline.json"` in the config,
  `check` fails only on findings that aren't in it. A finding matches by rule, field,
  logger and event name, with numbers and ids in the event masked; non-JSON lines match by
  a hash. An `adr_sensitive_data` entry also records what was found (`kind`: `key`,
  `nested`, `jwt`, `bearer_token`, `api_key`, `private_key`, `url_credentials`, `email`,
  `card_number`), so a known card number in a field doesn't hide a JWT in the same field.
  The result reports `baselined` (hidden as known), `baseline_fixed` (entries that no longer
  occur; re-run `logwarden baseline` to shrink the file) and `baseline_outdated`
  (`adr_sensitive_data` entries without a `kind`, written before v0.10.2: they hide
  nothing until the baseline is regenerated).
- **Disabled rules:** `disable = { rule_id = "reason" }`. A reason is required. Disabled
  rules and their reasons are listed in every result under `disabled`.
