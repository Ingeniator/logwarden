#!/usr/bin/env bash
# Run each recipe's example and check its output with logwarden (v0.6.0 claims 6-8).
#   examples/recipes/check.sh [node|node-otlp|go|java ...]     (default: all)
# Uses `uv run logwarden` from this checkout; Go runs in Docker if `go` isn't installed.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
lw() { uv run --quiet --project "$root" logwarden "$@"; }
export OTEL_SERVICE_NAME=orders-api SERVICE_VERSION=1.0.0 DEPLOYMENT_ENVIRONMENT=test

node_stdout() {
  cd "$here/node" && npm ci --silent --no-audit --no-fund
  node app.js > logs.jsonl
  lw check logs.jsonl --fields logwarden-fields.yaml
}
node_otlp() {
  cd "$here/node" && [ -d node_modules ] || npm ci --silent --no-audit --no-fund
  lw capture -o otlp.jsonl --fields logwarden-fields.yaml -- node --require ./otel.js app.js
}
go_slog() {
  cd "$here/go"
  if command -v go >/dev/null; then go run . > logs.jsonl
  else docker run --rm -v "$PWD:/src" -w /src golang:1.26 go run . > logs.jsonl; fi
  lw check logs.jsonl --fields logwarden-fields.yaml
}
java_logback() {
  cd "$here/java" && mvn -q -B compile exec:java > logs.jsonl
  lw check logs.jsonl --fields logwarden-fields.yaml
}

targets=("$@")
[ $# -eq 0 ] && targets=(node node-otlp go java)
for t in "${targets[@]}"; do
  echo "== $t"
  case "$t" in
    node) (node_stdout) ;;
    node-otlp) (node_otlp) ;;
    go) (go_slog) ;;
    java) (java_logback) ;;
    *) echo "unknown recipe $t"; exit 2 ;;
  esac
done
