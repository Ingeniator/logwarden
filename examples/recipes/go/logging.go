// log/slog set up to the logwarden logging standard. Copy this file into your service.
// https://github.com/Ingeniator/logwarden/blob/main/docs/recipes/go.md
package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"regexp"
	"strings"
	"time"

	"go.opentelemetry.io/otel/trace"
)

var environments = map[string]bool{"development": true, "staging": true, "test": true, "production": true}

var sensitiveKey = regexp.MustCompile(`(?i)(^|[._-])(password|passwd|secret|api[_-]?key|apikey|access[_-]?key|private[_-]?key|authorization|cookie|credentials?|token)$`)

// NewLogger returns a JSON logger whose lines pass logwarden check.
// Empty arguments fall back to OTEL_SERVICE_NAME, SERVICE_VERSION and DEPLOYMENT_ENVIRONMENT.
func NewLogger(w io.Writer, serviceName, serviceVersion, environment string, level slog.Level) (*slog.Logger, error) {
	serviceName = firstNonEmpty(serviceName, os.Getenv("OTEL_SERVICE_NAME"))
	serviceVersion = firstNonEmpty(serviceVersion, os.Getenv("SERVICE_VERSION"))
	environment = firstNonEmpty(environment, os.Getenv("DEPLOYMENT_ENVIRONMENT"))
	if serviceName == "" || serviceVersion == "" {
		return nil, fmt.Errorf("logging: service name and version are required")
	}
	if !environments[environment] {
		return nil, fmt.Errorf("logging: environment %q must be one of development, staging, test, production", environment)
	}
	h := slog.NewJSONHandler(w, &slog.HandlerOptions{Level: level, ReplaceAttr: standardAttr})
	return slog.New(traceHandler{h}).With(
		"service.name", serviceName,
		"service.version", serviceVersion,
		"deployment.environment.name", environment,
	), nil
}

// standardAttr renames slog's keys to the standard's and redacts sensitive fields.
func standardAttr(groups []string, a slog.Attr) slog.Attr {
	if len(groups) == 0 {
		switch a.Key {
		case slog.TimeKey:
			return slog.String("timestamp", a.Value.Time().UTC().Format(time.RFC3339Nano))
		case slog.LevelKey:
			l := strings.ToLower(a.Value.String())
			if l == "warn" {
				l = "warning"
			}
			return slog.String("level", l)
		case slog.MessageKey:
			return slog.Attr{Key: "event", Value: a.Value}
		}
	}
	if sensitiveKey.MatchString(a.Key) {
		return slog.String(a.Key, "[REDACTED]")
	}
	return a
}

// traceHandler adds trace_id and span_id of the span in ctx (use log.InfoContext(ctx, ...)).
type traceHandler struct{ slog.Handler }

func (h traceHandler) Handle(ctx context.Context, r slog.Record) error {
	if sc := trace.SpanContextFromContext(ctx); sc.IsValid() {
		r.AddAttrs(slog.String("trace_id", sc.TraceID().String()), slog.String("span_id", sc.SpanID().String()))
	}
	return h.Handler.Handle(ctx, r)
}
func (h traceHandler) WithAttrs(as []slog.Attr) slog.Handler { return traceHandler{h.Handler.WithAttrs(as)} }
func (h traceHandler) WithGroup(n string) slog.Handler       { return traceHandler{h.Handler.WithGroup(n)} }

// ErrorAttrs are the fields for a failed operation. Don't log it as "err" or "error" (banned synonyms).
func ErrorAttrs(err error) []any {
	return []any{"exception.type", fmt.Sprintf("%T", err), "exception.message", err.Error()}
}

func firstNonEmpty(a, b string) string {
	if a != "" {
		return a
	}
	return b
}
