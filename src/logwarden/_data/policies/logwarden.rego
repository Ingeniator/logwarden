package live_check_advice

# Logging-standard rules evaluated by `weaver registry live-check --advice-policies`.
# Weaver runs this once per sample (one log record, or one attribute of it),
# so rules here can only judge a single line. Rule ids and fix hints are
# documented in rules.json and docs/rules.md; keep all three in sync
# (tests/test_rules_wired.py checks it). Rules that need the raw line, such as
# adr_non_json_line, are in check.py.

import rego.v1

# banned key -> what to log instead
banned := {
	"status": "http.response.status_code",
	"status_code": "http.response.status_code",
	"path": "url.path or http.route",
	"method": "http.request.method",
	"url": "url.full",
	"host": "server.address",
	"model": "gen_ai.request.model",
	"traceback": "exception.stacktrace (log with exc_info=True)",
	"err": "exc_info=True or error.type",
	"error": "exc_info=True or error.type",
	"exception": "exception.type / exception.message (log with exc_info=True)",
	"uid": "user.id",
	"env": "deployment.environment.name",
	"headers": "specific safe fields; never log whole headers",
}

semconv_roots := ["http_", "db_", "url_", "server_", "client_", "exception_",
                  "service_", "user_", "gen_ai_", "code_", "messaging_"]

required := {"service.name", "service.version", "deployment.environment.name"}

environments := {"development", "staging", "test", "production"}

# §10: at least one of these must be on every warning/error line.
failure_keys := {"exception.type", "error.type", "http.response.status_code"}

make_advice(advice_type, advice_level, advice_context, message) := {
	"type": "advice",
	"advice_type": advice_type,
	"advice_level": advice_level,
	"advice_context": advice_context,
	"message": message,
}

present_keys := {a.name | some a in input.sample.log.attributes}

# §2 banned synonyms
deny contains make_advice("adr_banned_synonym", "violation", {"attribute_key": name}, msg) if {
	name := input.sample.attribute.name
	use := banned[name]
	msg := sprintf("§2: '%s' is a banned synonym; use %s", [name, use])
}

# §2 undotted key that shadows a semconv namespace
deny contains make_advice("adr_semconv_prefix", "violation", {"attribute_key": name}, msg) if {
	name := input.sample.attribute.name
	not contains(name, ".")
	some p in semconv_roots
	startswith(name, p)
	dotted := replace(replace(name, "_", "."), "gen.ai", "gen_ai")
	msg := sprintf("§2: '%s' uses '_' where semconv uses '.'; probably '%s'", [name, dotted])
}

# §3 documented deployment.environment.name values
deny contains make_advice("adr_environment_value", "violation", {"attribute_key": name, "value": v}, msg) if {
	name := input.sample.attribute.name
	name == "deployment.environment.name"
	v := input.sample.attribute.value
	not environments[v]
	msg := sprintf("§3: environment '%s' must be one of development, staging, test, production", [v])
}

# §3 required resource fields on every record
deny contains make_advice("adr_missing_required", "violation", {"attribute_key": k}, msg) if {
	input.sample.log
	some k in required
	not present_keys[k]
	msg := sprintf("§3: required field '%s' missing", [k])
}

# §4 event must be constant snake_case
deny contains make_advice("adr_event_name", "violation", {"event": ev}, msg) if {
	input.sample.log
	ev := input.sample.log.body
	not regex.match(`^[a-z][a-z0-9_]*$`, ev)
	msg := sprintf("§4: event '%s' must be a constant snake_case name", [ev])
}

# §10 warning/error lines and *_failed events must say what failed
deny contains make_advice("adr_failure_context", "violation", {"event": ev}, msg) if {
	input.sample.log
	ev := input.sample.log.body
	is_failure(input.sample.log)
	count(failure_keys & present_keys) == 0
	msg := sprintf("§10: '%s' is a failure line without exception.type, error.type or http.response.status_code", [ev])
}

is_failure(log) if log.severity_number >= 13

is_failure(log) if endswith(log.body, "_failed")

# §8 never log secrets or personal data. Messages name the field and the kind of data,
# never the value, so findings are safe to print in CI.

# Last segment of the key, e.g. password, client_secret, csrf_token, api-key.
# Not token counts: gen_ai.usage.input_tokens, token_count.
sensitive_key := `(?i)(^|[._-])(password|passwd|secret|api[_-]?key|apikey|access[_-]?key|private[_-]?key|authorization|cookie|set[_-]?cookie|credentials?|token)$`

# The same names as keys inside a nested JSON value with a string value that isn't redacted.
sensitive_nested := `(?i)"([a-z0-9]*[._-])?(password|passwd|secret|api[_-]?key|apikey|access[_-]?key|private[_-]?key|authorization|cookie|credentials?|token)"\s*:\s*"[^"*\[<]`

redacted := `(?i)^(|\*+|\[?redacted\]?|<redacted>|\[filtered\]|\[masked\]|x{3,})$`

secret_values := {
	"a JWT": `eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}`,
	"a Bearer token": `(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}`,
	"an API key": `\b(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}|glpat-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35})`,
	"a private key": `-----BEGIN [A-Z ]*PRIVATE KEY-----`,
	"credentials in a URL": `[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]*:[^/\s@]+@`,
	"an email address": `[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}`,
}

attr_text(v) := v if is_string(v)

attr_text(v) := format_int(v, 10) if {
	is_number(v)
	v == floor(v)
}

deny contains make_advice("adr_sensitive_data", "violation", {"attribute_key": name, "match": "key"}, msg) if {
	name := input.sample.attribute.name
	regex.match(sensitive_key, name)
	v := input.sample.attribute.value
	not is_boolean(v)
	not regex.match(redacted, attr_text(v))
	msg := sprintf("§8: '%s' holds a secret or credential; don't log it (log an id or a redacted value)", [name])
}

deny contains make_advice("adr_sensitive_data", "violation", {"attribute_key": name}, msg) if {
	name := input.sample.attribute.name
	not regex.match(sensitive_key, name)
	v := input.sample.attribute.value
	is_string(v)
	regex.match(sensitive_nested, v)
	msg := sprintf("§8: '%s' contains a nested secret or credential field; don't log it", [name])
}

deny contains make_advice("adr_sensitive_data", "violation", {"attribute_key": name}, msg) if {
	name := input.sample.attribute.name
	some kind, pattern in secret_values
	regex.match(pattern, attr_text(input.sample.attribute.value))
	msg := sprintf("§8: '%s' looks like it contains %s; don't log it", [name, kind])
}

deny contains make_advice("adr_sensitive_data", "violation", {"attribute_key": name}, msg) if {
	name := input.sample.attribute.name
	some candidate in regex.find_n(`\b[2-6](?:[ -]?\d){12,18}\b`, attr_text(input.sample.attribute.value), -1)
	digits := regex.replace(candidate, `[ -]`, "")
	luhn_valid(digits)
	msg := sprintf("§8: '%s' looks like it contains a card number; don't log it", [name])
}

luhn_valid(s) if {
	ds := [to_number(c) | some c in split(s, "")]
	n := count(ds)
	n >= 13
	n <= 19
	total := sum([luhn_term(ds[(n - 1) - i], i) | some i in numbers.range(0, n - 1)])
	total % 10 == 0
}

luhn_term(d, i) := d if i % 2 == 0

luhn_term(d, i) := d * 2 if {
	i % 2 == 1
	d * 2 <= 9
}

luhn_term(d, i) := (d * 2) - 9 if {
	i % 2 == 1
	d * 2 > 9
}

# §3/§7 W3C trace context: lowercase hex, 32 chars for trace_id and 16 for span_id, not all zeros.
deny contains make_advice("adr_trace_context", "violation", {"attribute_key": "trace_id"}, msg) if {
	input.sample.log
	t := input.sample.log.trace_id
	t != null
	not w3c_id(t, 32)
	msg := "§7: trace_id must be 32 lowercase hex characters (W3C), not all zeros"
}

deny contains make_advice("adr_trace_context", "violation", {"attribute_key": "span_id"}, msg) if {
	input.sample.log
	t := input.sample.log.span_id
	t != null
	not w3c_id(t, 16)
	msg := "§7: span_id must be 16 lowercase hex characters (W3C), not all zeros"
}

deny contains make_advice("adr_trace_context", "violation", {"attribute_key": "span_id"}, msg) if {
	input.sample.log
	input.sample.log.span_id != null
	input.sample.log.trace_id == null
	msg := "§7: span_id without trace_id; log both from the active span"
}

w3c_id(t, n) if {
	is_string(t)
	regex.match(sprintf("^[0-9a-f]{%d}$", [n]), t)
	not regex.match(`^0+$`, t)
}
