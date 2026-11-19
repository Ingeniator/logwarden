package main

import (
	"context"
	"fmt"
	"log/slog"
)

func main() {
	log := slog.Default()
	ctx := context.Background()
	n, user, pw := 3, "u", "x"
	var err error

	// ruleid: logwarden-event-name-go
	log.Info(fmt.Sprintf("Imported %d rows", n))
	// ruleid: logwarden-event-name-go
	slog.ErrorContext(ctx, "failed for "+user)
	// ruleid: logwarden-event-name-go
	log.Warn("Order not found", "order_id", 1)
	// ok: logwarden-event-name-go
	log.InfoContext(ctx, "import_completed", "rows_imported", n)

	// ruleid: logwarden-sensitive-key-go
	log.Info("login_attempted", "password", pw)
	// ruleid: logwarden-sensitive-key-go
	log.Info("call_made", slog.String("api_key", pw))
	// ok: logwarden-sensitive-key-go
	log.Info("login_attempted", "user_id", user)

	// ruleid: logwarden-banned-key-go
	log.Error("upstream_call_failed", "err", err)
	// ruleid: logwarden-banned-key-go
	log.Error("upstream_call_failed", slog.Any("error", err))
	// ok: logwarden-banned-key-go
	log.Error("upstream_call_failed", "error.type", "timeout")

	// ruleid: logwarden-print-go
	fmt.Println("starting")
	// ok: logwarden-print-go
	log.Info("service_started")
}
