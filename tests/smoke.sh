#!/usr/bin/env bash
# Smoke test: good.jsonl passes, bad.jsonl fails on every logwarden rule,
# stdin and fields files work, findings point at the right input lines.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LW="$ROOT/bin/logwarden"
fail=0
pass() { echo "PASS $*"; }
bad() { echo "FAIL $*"; fail=1; }

if out="$("$LW" check "$ROOT/examples/good.jsonl")"; then pass "good.jsonl has no violations"
else bad "good.jsonl reported violations:"; echo "$out"; fi

res="$("$LW" check --json "$ROOT/examples/bad.jsonl")"; rc=$?
[[ $rc -eq 1 ]] && pass "bad.jsonl exits 1" || bad "bad.jsonl exit=$rc"

for rule in adr_missing_required adr_banned_synonym adr_semconv_prefix \
            adr_environment_value adr_event_name adr_failure_context missing_attribute; do
  jq -e --arg r "$rule" '.by_rule | any(.rule == $r)' <<<"$res" >/dev/null \
    && pass "bad.jsonl caught $rule" || bad "bad.jsonl missed $rule"
done

jq -e '.findings | any(.rule == "adr_failure_context" and .line == 3)' <<<"$res" >/dev/null \
  && pass "finding points at input line 3" || bad "wrong line number for adr_failure_context"
jq -e '.skipped_lines == 1' <<<"$res" >/dev/null \
  && pass "non-JSON line counted as skipped" || bad "skipped_lines wrong"
jq -e '[.findings[] | select(.field == "status") | .rule] == ["adr_banned_synonym"]' <<<"$res" >/dev/null \
  && pass "banned synonym not double-reported as missing_attribute" || bad "status reported twice"

# stdin + project fields file: job_id is unknown until declared
line='{"level":"info","event":"job_done","logger":"w","service.name":"s","service.version":"1","deployment.environment.name":"test","job_id":"j1"}'
echo "$line" | "$LW" check - >/dev/null && bad "undeclared job_id passed" || pass "undeclared field fails (stdin)"
fields="$(mktemp)"; trap 'rm -f -- "${fields:?}"' EXIT
cat > "$fields" <<'EOF'
groups:
  - id: registry.smoke.log
    type: attribute_group
    brief: smoke
    attributes:
      - id: job_id
        type: string
        stability: development
        brief: Job id.
        examples: ["j1"]
EOF
echo "$line" | "$LW" check - --fields "$fields" >/dev/null && pass "--fields declares a project field" || bad "--fields did not apply"

"$LW" rules adr_event_name | grep -q '^fix:' && pass "rules explains a rule" || bad "rules output"

exit $fail
