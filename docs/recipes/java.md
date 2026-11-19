# Java: logback

Logs that pass `logwarden check`, from SLF4J + logback with
[logstash-logback-encoder](https://github.com/logfellow/logstash-logback-encoder). The
runnable example is in [`examples/recipes/java`](../../examples/recipes/java); CI runs it
and checks its output.

## Setup

Dependencies: `ch.qos.logback:logback-classic` and
`net.logstash.logback:logstash-logback-encoder` (see the example's `pom.xml`). Copy
[`logback.xml`](../../examples/recipes/java/src/main/resources/logback.xml) into
`src/main/resources`. It uses a composite JSON encoder with only the providers the
standard wants:

| Provider | Gives | Standard |
|---|---|---|
| `timestamp` | `"timestamp"` in UTC | §3 |
| `pattern` (nested `%replace`) | `"level": "info"`, `WARN` → `warning`, `TRACE` → `debug` | §5 |
| `message` | the message as `event` | §4 |
| `loggerName` | `logger` | §3 |
| `pattern` with `${OTEL_SERVICE_NAME}`, ... | resource fields from env vars or `-D` properties | §3 |
| `mdc` | `trace_id`/`span_id`, which the OpenTelemetry Java agent puts in the MDC | §7 |
| `arguments` | fields from `kv("order_id", 42)` | §2 |
| `throwableClassName`, `throwableMessage`, `stackTrace` | `exception.type` (full class name), `exception.message`, `exception.stacktrace` | §6 |

Set `OTEL_SERVICE_NAME`, `SERVICE_VERSION` and `DEPLOYMENT_ENVIRONMENT`. If one is missing,
logback writes `OTEL_SERVICE_NAME_IS_UNDEFINED`, which the check reports.

## Call sites

```java
import static net.logstash.logback.argument.StructuredArguments.kv;

log.info("order_created", kv("order_id", 42));
log.error("payment_call_failed", kv("server.address", "payments.example.com"), e);  // exception last
log.warn("payment_call_retried", kv("error.type", "timeout"));
```

The message is a constant event name: SLF4J placeholders (`"Imported {} rows"`) and string
concatenation fail §4, and `logwarden lint-code` finds them in source.

## Check it

```bash
mvn -q compile exec:java > logs.jsonl && logwarden check logs.jsonl
```

With the OpenTelemetry Java agent exporting logs, use
`logwarden capture -- ./gradlew test` (or `mvn test`) instead; it sets the OTLP endpoint and
`OTEL_LOGS_EXPORTER=otlp`, and the agent's logback appender sends each record.
(This route isn't run in logwarden's CI; the Node recipe's OTLP route is.)
