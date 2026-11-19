// Example service code: what logging looks like at call sites.
package main

import (
	"context"
	"log/slog"
	"net"
	"os"

	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

func main() {
	log, err := NewLogger(os.Stdout, "orders-api", "1.0.0", "test", slog.LevelInfo)
	if err != nil {
		panic(err)
	}
	log = log.With("logger", "orders")

	// Stand-in for your service's tracing setup, so there is an active span.
	ctx, span := sdktrace.NewTracerProvider().Tracer("orders").Start(context.Background(), "create_order")
	defer span.End()

	log.InfoContext(ctx, "order_created", "order_id", 42)
	log.DebugContext(ctx, "order_cached", "order_id", 42)
	_, dialErr := net.Dial("tcp", "127.0.0.1:1")
	log.ErrorContext(ctx, "payment_call_failed",
		append([]any{"order_id", 42, "server.address", "payments.example.com"}, ErrorAttrs(dialErr)...)...)
	log.WarnContext(ctx, "payment_call_retried", "error.type", "timeout", "server.address", "payments.example.com")
}
