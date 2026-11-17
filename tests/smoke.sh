#!/usr/bin/env bash
# Smoke test: good.jsonl must pass, bad.jsonl must fail on each ADR rule.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LW="$ROOT/bin/logwarden"
fail=0

if "$LW" check "$ROOT/examples/good.jsonl" >/dev/null; then
  echo "PASS good.jsonl has no violations"
else
  echo "FAIL good.jsonl reported violations:"; "$LW" check "$ROOT/examples/good.jsonl"; fail=1
fi

summary="$("$LW" check --json "$ROOT/examples/bad.jsonl")"
rc=$?
[[ $rc -eq 1 ]] && echo "PASS bad.jsonl exits 1" || { echo "FAIL bad.jsonl exit=$rc"; fail=1; }

for rule in adr_missing_required adr_banned_synonym adr_semconv_prefix adr_environment_value adr_event_name adr_failure_context; do
  if jq -e --arg r "$rule" '.by_rule | any(.rule == $r)' <<<"$summary" >/dev/null; then
    echo "PASS bad.jsonl caught $rule"
  else
    echo "FAIL bad.jsonl missed $rule"; fail=1
  fi
done

exit $fail
