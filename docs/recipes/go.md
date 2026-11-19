# Go: log/slog

Logs that pass `logwarden check`, from the standard library's `log/slog`. The runnable
example is in [`examples/recipes/go`](../../examples/recipes/go); CI runs it and checks its
output.

## Setup

Copy [`logging.go`](../../examples/recipes/go/logging.go) into the service (rename the
package). It needs `go.opentelemetry.io/otel/trace` for the trace ids.

```go
log, err := NewLogger(os.Stdout, "orders-api", "1.0.0", "production", slog.LevelInfo)
if err != nil { panic(err) }
log = log.With("logger", "orders")
```

What it sets:

| Piece | Gives | Standard |
|---|---|---|
| `slog.NewJSONHandler` | one JSON object per line | §1 |
| `ReplaceAttr` | `time` → `timestamp` (RFC 3339, UTC), `level` lower-case with `WARN` → `warning`, `msg` → `event`, sensitive keys → `[REDACTED]` | §3, §4, §5, §8 |
| `.With(...)` | `service.name`, `service.version`, `deployment.environment.name` on every line | §3 |
| `traceHandler` | `trace_id`/`span_id` of the span in the context (use the `...Context` methods) | §7 |

Empty arguments fall back to `OTEL_SERVICE_NAME`, `SERVICE_VERSION` and
`DEPLOYMENT_ENVIRONMENT`; a missing name/version or an environment like `prod` is an error.

## Call sites

```go
log.InfoContext(ctx, "order_created", "order_id", 42)
if err != nil {
    log.ErrorContext(ctx, "payment_call_failed",
        append([]any{"server.address", "payments.example.com"}, ErrorAttrs(err)...)...)
}
log.WarnContext(ctx, "payment_call_retried", "error.type", "timeout")
```

Don't log errors as `"err"` or `"error"` (banned synonyms, §2); `ErrorAttrs` gives
`exception.type` (the Go type, e.g. `*net.OpError`) and `exception.message`.
`log.Info(fmt.Sprintf(...))` fails §4. `logwarden lint-code`, or sloglint
(`docs/integration.md` §6), finds it in source.

## Check it

```bash
go run . > logs.jsonl && logwarden check logs.jsonl
```

With the OpenTelemetry Go logs SDK (`otelslog` bridge + OTLP/HTTP exporter), use
`logwarden capture -- go test ./...` instead; it sets `OTEL_EXPORTER_OTLP_ENDPOINT`.
(This route isn't run in logwarden's CI; the Node recipe's OTLP route is.)
