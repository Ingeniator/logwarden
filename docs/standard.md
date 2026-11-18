# Logging standard

What a good log line looks like, for any service. logwarden checks the parts marked
**[checked]**. The rest is for code review. Each rule id links to its entry in
[rules.md](rules.md).

The standard builds on the OpenTelemetry
[Logs Data Model](https://opentelemetry.io/docs/specs/otel/logs/data-model/) and
[semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/)
(semconv), so logs use the same names as traces and metrics.

## 1. Format

- **[checked]** One JSON object per line, written to stdout. The platform collects it. The
  service doesn't write log files or ship logs itself.
  [`adr_non_json_line`](rules.md#adr_non_json_line)
- Third-party library logs go through the same formatter, so every line is JSON.
- Human-readable console output is for local development only.
- `timestamp` is ISO-8601 UTC.

## 2. Field names

- **[checked]** If semconv has a name for something, use it. Don't invent a synonym.
  [`adr_banned_synonym`](rules.md#adr_banned_synonym),
  [`adr_semconv_prefix`](rules.md#adr_semconv_prefix),
  [`missing_attribute`](rules.md#missing_attribute),
  [`deprecated`](rules.md#deprecated)

  | Concept | Use | Not |
  |---|---|---|
  | HTTP | `http.request.method`, `http.route`, `url.path`, `http.response.status_code` | `method`, `path`, `status` |
  | Errors | `exception.type`, `exception.message`, `exception.stacktrace`, `error.type` | `error`, `err`, `traceback` |
  | Outbound calls | `server.address`, `server.port` | `host`, `upstream` |
  | DB | `db.system.name`, `db.operation.name`, `db.collection.name` | `table` |
  | LLM | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` | `model`, `tokens_in` |
  | User | `user.id` | `uid`, `username` |

- **Domain fields** with no semconv name are `snake_case` without dots: `job_id`,
  `rows_imported`. They must not start with a semconv namespace (`http_`, `db_`, `user_`, …).
  Declare them in `logwarden-fields.yaml`, otherwise they fail as unknown.
- Units go in the name: `duration_ms`, `size_bytes`.

## 3. Required fields

**[checked]** Every line has:

| Field | Example |
|---|---|
| `timestamp`, `level`, `event`, `logger` | |
| `service.name` | `billing-api` |
| `service.version` | `1.4.2` |
| `deployment.environment.name` | one of `development`, `staging`, `test`, `production` |
| `trace_id`, `span_id` | when a trace span is active (W3C hex) |

Add the resource fields in one shared processor or formatter, not at each call site.
[`adr_missing_required`](rules.md#adr_missing_required),
[`adr_environment_value`](rules.md#adr_environment_value)

## 4. Event names

**[checked]** `event` is a constant snake_case name, like `import_completed` or
`upstream_call_failed`. Variable data goes in fields.
[`adr_event_name`](rules.md#adr_event_name)

```python
log.info(f"Imported {n} rows")                 # ❌
log.info("import_completed", rows_imported=n)  # ✅
```

## 5. Levels

| Level | For |
|---|---|
| `error` | The operation failed and someone needs to look at it. |
| `warning` | Recovered, degraded or unexpected (a retry, a fallback). |
| `info` | Lifecycle and business events, a few per request or job. Never inside per-item loops. |
| `debug` | Developer detail. Off in production. |

## 6. Exceptions

- Log an exception once, where it's handled. Don't log it and then re-raise.
- Log it with the exception attached (`exc_info=True` / `log.exception`), so that
  `exception.type`, `exception.message` and `exception.stacktrace` are filled in. Never
  log `error=str(e)`.

## 7. Correlation

- Add `trace_id`/`span_id` from the active OTel span in a shared processor.
- **[checked]** `trace_id` is 32 and `span_id` 16 lowercase hex characters (W3C), never all
  zeros, and `span_id` only with a `trace_id`. Omit both when no span is active.
  [`adr_trace_context`](rules.md#adr_trace_context)
- Bind `request_id` and `job_id` once per request or job (contextvars), and clear them at
  the start of each request or job.

## 8. Never log

Secrets, tokens, auth headers, credentials in DSNs. LLM prompts and completions, chat
messages, user-uploaded data, full request or response bodies. Personal data unless it's
required. Log IDs, sizes and counts instead.

**[checked]** for secret-named fields and for values that look like tokens, keys,
credentials in URLs, email addresses or card numbers.
[`adr_sensitive_data`](rules.md#adr_sensitive_data). Prompts, chat content and bodies
can't be recognised by pattern, so they stay a code-review item.

## 9. Noise

Silence health-check and metrics paths. Set chatty third-party loggers to `WARNING`.
Logs explain *why*. Counts and latencies belong in metrics.

## 10. Debuggable failures

A failure line must be enough to diagnose the problem without turning on debug.

- **[checked]** Every `warning`/`error` line and every `*_failed` event has
  `exception.type`, `error.type` or `http.response.status_code`.
  [`adr_failure_context`](rules.md#adr_failure_context)
- Log the cause at `warning`/`error`. Never log it only at `debug`.
- For a failed outbound call, log:

  | Field | Example |
  |---|---|
  | `server.address` | `api.provider.example` |
  | `http.response.status_code` | `429` |
  | `error.type` | `rate_limit_exceeded` (upstream code, or the exception class) |
  | `duration_ms` | `30012.4` |
  | `upstream_request_id` | the upstream's request id header |
  | `upstream_error_message` | the upstream error message, scrubbed, at most 500 chars |

- Never log the raw upstream response body.

```python
except httpx.HTTPStatusError as e:
    log.error("upstream_call_failed", exc_info=True,
              duration_ms=elapsed_ms,
              upstream_request_id=e.response.headers.get("x-request-id"),
              **{"server.address": e.response.request.url.host,
                 "http.response.status_code": e.response.status_code,
                 "error.type": extract_error_code(e.response) or type(e).__name__})
    raise
```

## Reference Python setup (structlog)

```python
shared = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    add_service_resource,      # service.name / service.version / deployment.environment.name
    add_otel_trace_context,    # trace_id / span_id
    exception_to_semconv,      # exception.type / .message / .stacktrace
    redact_sensitive,
]
# stdlib loggers: logging.Handler with structlog.stdlib.ProcessorFormatter(
#     foreign_pre_chain=shared, processor=structlog.processors.JSONRenderer())

def add_otel_trace_context(_, __, event_dict):
    ctx = opentelemetry.trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict
```

Notes: `gen_ai.*` attributes now live in the
[OTel GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai)
repository and are still changing. Follow logwarden's `deprecated` findings when they're renamed.
