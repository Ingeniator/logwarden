# logwarden

Checks service logs against the ai-suite structured logging standard
([ADR-0001](https://github.com/Ingeniator/ai-suite/blob/main/docs/adr/0001-structured-logging-standard.md)):
OpenTelemetry semantic-convention field names, required resource fields, constant
event names and failure context on every warning or error.

It wraps [OTel Weaver](https://github.com/open-telemetry/weaver) `registry live-check`
and adds the ADR rules as a Rego policy. Any repo can use it, whatever language
the service is written in, as long as it writes JSON-lines logs.

```
$ logwarden check test-logs.jsonl
logwarden: 570 log records, 3063 violations
  1710  adr_missing_required
          e.g. ADR-0001 §3: required field 'service.version' missing
  536   adr_event_name
          e.g. ADR-0001 §4: event 'Loaded 3 models' must be constant snake_case
  ...
```

The command exits with code 1 if it finds any violation, so it can serve as a CI gate.

## Rules

| Rule id | ADR | Fails when |
|---|---|---|
| `adr_banned_synonym` | §2 | key is a banned synonym (`status`, `path`, `model`, `err`, ...) |
| `adr_semconv_prefix` | §2 | key uses `_` where semconv uses `.` (`http_route`) |
| `adr_missing_required` | §3 | `service.name`, `service.version` or `deployment.environment.name` missing |
| `adr_environment_value` | §3 | environment is not `development` / `staging` / `test` / `production` |
| `adr_event_name` | §4 | `event` is not constant snake_case |
| `adr_failure_context` | §12 | warning/error or `*_failed` line has none of `exception.type`, `error.type`, `http.response.status_code` |
| `missing_attribute` | §2 | key is neither in OTel semconv v1.45.0 nor in `registry/logwarden.yaml` |
| `deprecated` | §2 | key is deprecated in semconv (Weaver built-in) |

## Requirements

- `bash` and `jq`
- `weaver` (OTel Weaver, v0.28.0 or later). Run `make install-weaver` to download a
  pinned copy into `.tools/`, or point `WEAVER` at an existing binary.

## Usage

### Locally

```bash
make test                                   # smoke test on examples/
make check LOGS=../yallmp/test-logs.jsonl   # check a log file
bin/logwarden check logs.jsonl -o out/      # keep Weaver's full live_check.json
bin/logwarden check logs.jsonl --json       # machine-readable summary
```

### In another repo's CI (GitHub Actions)

Capture JSON logs during the test run, then:

```yaml
- uses: Ingeniator/logwarden@main
  with:
    logs: test-logs.jsonl
    # registry: telemetry/   # optional overlay with service-specific fields
```

### Service-specific fields

Fields that are neither semconv nor in `registry/logwarden.yaml` are reported as
`missing_attribute`. To allow a field only in your service, add an overlay
registry to your repo and pass it with `--registry`. The overlay's `manifest.yaml`
must list both semconv and logwarden as dependencies.

## Layout

```
bin/logwarden          CLI (check; report is planned)
lib/to-weaver.jq       structlog JSON line -> Weaver log sample
lib/summary.jq         Weaver live_check.json -> rule counts
registry/              Weaver registry: semconv v1.45.0 + common domain fields
policies/              ADR rules in Rego (package live_check_advice)
config/weaver.toml     finding filters
examples/              good.jsonl / bad.jsonl used by tests/smoke.sh
action.yml             composite GitHub Action
```

## Limits

- Policies run per log line, so they can't check things across a whole run, such as
  "every upstream in `upstreams.yaml` has a failure log" (ADR §12, failure-path
  coverage). That check is planned as a jq pass in `lib/`.
- Non-JSON lines are skipped, not reported. A §1 "JSON only on stdout" check is planned.
- Source-level rules (Ruff `G`/`LOG`, Semgrep) live in the ADR's Enforcement
  section, not here.

## Roadmap

- [ ] `logwarden report`: an HTML report with per-rule bars, examples and a diff
      against a base branch
- [ ] Run-level checks: failure-path coverage against `upstreams.yaml`, §1 non-JSON lines
- [ ] Ship Semgrep rules from the ADR as `rules/semgrep/`
- [ ] Publish versioned releases (tags) so consumers can pin `@v0.1.0`
