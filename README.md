# logwarden

A linter for **log output**. You give it the JSON logs your service actually
wrote, and it reports every line that breaks the
[logging standard (ADR-0001)](https://github.com/Ingeniator/ai-suite/blob/main/docs/adr/0001-structured-logging-standard.md).
It exits with code 1 when it finds a problem, so CI can fail on it.

It checks what the service emitted at runtime, not the source code, so it works
for any language or logging library.

```
$ logwarden check logs.jsonl
logwarden: 570 log records, 3063 violations
  1710  adr_missing_required
          e.g. ADR-0001 §3: required field 'service.version' missing
  536   adr_event_name
          e.g. ADR-0001 §4: event 'Loaded 3 models' must be constant snake_case
```

## What it checks

| Rule | Example of a failing line |
|---|---|
| Field names follow OTel semconv (§2) | `status=200` instead of `http.response.status_code=200` |
| Unknown or deprecated fields (§2) | `foo_bar` isn't in semconv or the allowed list |
| Required fields (§3) | no `service.name`, `service.version` or `deployment.environment.name` |
| Environment values (§3) | `env=dev` instead of `development` |
| Constant event names (§4) | `event="Loaded 3 models"` instead of `models_loaded` |
| Failures say what failed (§12) | an error line without `error.type`, `exception.type` or a status code |

## Use it in your project

**1. Capture the logs.** Run the service (tests, or `docker compose` smoke) with
JSON logging and save stdout:

```bash
docker compose logs --no-log-prefix myservice > logs.jsonl
```

**2. Check them in CI:**

```yaml
- uses: Ingeniator/logwarden@v0.1.0
  with:
    logs: logs.jsonl
```

To run it locally, use `make check LOGS=logs.jsonl` inside this repo. It downloads
[OTel Weaver](https://github.com/open-telemetry/weaver) on first run and needs `jq`.

**3. Allow fields that only your service uses.** Fields that are neither OTel
semconv nor in `registry/logwarden.yaml` fail as `missing_attribute`. If a field
is used by several services, add it to `registry/logwarden.yaml` here. If only
your service uses it, keep a small registry in your repo and pass it with
`--registry` (`registry:` in the GitHub Action).

## How it works

It converts each log line into a sample for OTel Weaver `registry live-check`. Weaver
compares the sample with the semconv registry and runs the ADR rules in
`policies/logwarden.rego`. `make test` checks that the line in
`examples/good.jsonl` passes and that every line in `examples/bad.jsonl` fails.

## Not covered (yet)

- Source-code rules (Ruff `G`/`LOG`, Semgrep) are listed in the ADR.
- Checks across the whole run, such as "every upstream has a failure-path log".
- Non-JSON lines, which are currently skipped.
- `logwarden report`, an HTML report, is planned.
