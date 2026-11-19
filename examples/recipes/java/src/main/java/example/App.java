// Example service code: what logging looks like at call sites.
package example;

import static net.logstash.logback.argument.StructuredArguments.kv;

import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanContext;
import io.opentelemetry.context.Scope;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import io.opentelemetry.sdk.trace.export.SpanExporter;
import java.net.ConnectException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;

public class App {
    private static final Logger log = LoggerFactory.getLogger("orders");

    public static void main(String[] args) {
        // Stand-in for the OpenTelemetry Java agent: an active span, its ids in the MDC.
        SdkTracerProvider tracing = SdkTracerProvider.builder()
                .addSpanProcessor(SimpleSpanProcessor.create(SpanExporter.composite()))
                .build();
        Span span = tracing.get("orders").spanBuilder("create_order").startSpan();
        try (Scope ignored = span.makeCurrent()) {
            SpanContext sc = span.getSpanContext();
            MDC.put("trace_id", sc.getTraceId());
            MDC.put("span_id", sc.getSpanId());

            log.info("order_created", kv("order_id", 42));
            log.debug("order_cached", kv("order_id", 42));
            try {
                throw new ConnectException("connection refused");
            } catch (ConnectException e) {
                // The exception as the last argument fills exception.type/.message/.stacktrace.
                log.error("payment_call_failed", kv("order_id", 42), kv("server.address", "payments.example.com"), e);
            }
            log.warn("payment_call_retried", kv("error.type", "timeout"), kv("server.address", "payments.example.com"));
        } finally {
            MDC.clear();
            span.end();
        }
    }
}
