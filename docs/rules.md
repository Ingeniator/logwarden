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
- Non-JSON lines are counted as `skipped_lines`. They don't fail the check yet, but under
  standard §1 they shouldn't exist.
