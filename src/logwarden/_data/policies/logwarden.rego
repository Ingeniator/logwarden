package live_check_advice

# Logging-standard rules evaluated by `weaver registry live-check --advice-policies`.
# Weaver runs this once per sample (one log record, or one attribute of it),
# so rules here can only judge a single line. Rule ids and fix hints are
# documented in lib/rules.json and docs/rules.md; keep all three in sync.

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

# §12: at least one of these must be on every warning/error line.
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

# §12 warning/error lines and *_failed events must say what failed
deny contains make_advice("adr_failure_context", "violation", {"event": ev}, msg) if {
	input.sample.log
	ev := input.sample.log.body
	is_failure(input.sample.log)
	count(failure_keys & present_keys) == 0
	msg := sprintf("§12: '%s' is a failure line without exception.type, error.type or http.response.status_code", [ev])
}

is_failure(log) if log.severity_number >= 13

is_failure(log) if endswith(log.body, "_failed")
