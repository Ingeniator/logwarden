# Node: pino

Logs that pass `logwarden check`, from [pino](https://getpino.io). The runnable example is
in [`examples/recipes/node`](../../examples/recipes/node); CI runs it and checks its output.

## Setup

Copy [`log.js`](../../examples/recipes/node/log.js) into the service:

```bash
npm install pino @opentelemetry/api
```

```js
const { createLogger, errorFields } = require('./log');
const log = createLogger({ serviceName: 'orders-api', serviceVersion: '1.0.0', environment: 'production' })
  .child({ logger: 'orders' });
```

What it sets, and why:

| pino option | Gives | Standard |
|---|---|---|
| `messageKey: 'event'` | the message is the event name | §4 |
| `timestamp` | `"timestamp"` in ISO-8601 UTC | §3 |
| `formatters.level` | `"level": "info"`, `warn` → `warning` | §5 |
| `base` | `service.name`, `service.version`, `deployment.environment.name` instead of `pid`/`hostname` | §3 |
| `mixin` | `trace_id`/`span_id` of the active OpenTelemetry span | §7 |
| `redact` | `[REDACTED]` for password/token/... fields, as a safety net | §8 |

Arguments fall back to `OTEL_SERVICE_NAME`, `SERVICE_VERSION` and `DEPLOYMENT_ENVIRONMENT`;
a missing name/version or an environment like `prod` throws at startup.

## Call sites

```js
log.info({ order_id: 42 }, 'order_created');                      // fields first, then the event
try { await pay(order) } catch (err) {
  log.error({ order_id: 42, 'server.address': 'payments.example.com', ...errorFields(err) },
            'payment_call_failed');                                 // exception.type/.message/.stacktrace
}
log.warn({ 'error.type': 'timeout' }, 'payment_call_retried');
```

Don't pass the error as `err` (pino's default) or `error`: both are banned synonyms (§2).
`log.info(\`Imported ${n} rows\`)` fails §4; `logwarden lint-code` finds it in source.

## Check it

```bash
node app.js > logs.jsonl && logwarden check logs.jsonl       # or: npm test > logs.jsonl
```

## Or send logs over OTLP

With the OpenTelemetry SDK, pino's records can go to a collector instead of (or as well as)
stdout. [`otel.js`](../../examples/recipes/node/otel.js) registers
`@opentelemetry/instrumentation-pino` and an OTLP/HTTP log exporter; load it before the app.
`logwarden capture` receives those logs and checks them:

```bash
npm install @opentelemetry/api-logs @opentelemetry/sdk-logs @opentelemetry/exporter-logs-otlp-http \
            @opentelemetry/instrumentation @opentelemetry/instrumentation-pino @opentelemetry/resources
logwarden capture -o logs.jsonl -- node --require ./otel.js app.js
```

The example pins the OpenTelemetry JS 2.x line (`@opentelemetry/api` 1.9,
`sdk-logs` 0.223): at the time of writing the 3.x line has no `sdk-trace-base` that accepts
`@opentelemetry/api` 1.10.
