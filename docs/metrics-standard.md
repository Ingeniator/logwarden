# Metrics standard

**Status: draft.** Rules marked **[checked]** are checked by `logwarden check --signal metrics`
(experimental, from [v0.12.0](releases/v0.12.0.md); see [Checking metrics](#checking-metrics)).
Rules marked **[v0.13.0]** are planned for [v0.13.0](releases/v0.13.0.md). The rest is for code
review. Rule ids link to [rules.md](rules.md).

This standard adopts existing ones rather than inventing names:

- [OpenTelemetry metrics semantic conventions](https://opentelemetry.io/docs/specs/semconv/general/metrics/)
  (semconv): naming rules and hundreds of defined metrics. This is the same registry the
  [logging standard](standard.md) uses, so logs and metrics share attribute names.
- [Prometheus naming practices](https://prometheus.io/docs/practices/naming/) and
  [OpenMetrics](https://prometheus.io/docs/specs/om/open_metrics_spec/), for the exported form.
- The [OTel → Prometheus compatibility spec](https://opentelemetry.io/docs/specs/otel/compatibility/prometheus_and_openmetrics/),
  which defines how one becomes the other.

What this standard adds is what those leave open: one source of truth, label rules shared with
logs, cardinality budgets, sensitive data and resource identity.

## 1. One source of truth

- Metric names are defined once, as OTel names in code and in the registry
  (`http.server.request.duration`). The Prometheus name
  (`http_server_request_duration_seconds`) is derived by the exporter, never written by hand.
- **[checked]** A scraped Prometheus name must be the translation of a registered OTel name:
  dots become `_`, the unit becomes a suffix (`s` → `_seconds`, `By` → `_bytes`, `1` → `_ratio`),
  and counters get `_total`. Prometheus 3 can also keep the dotted names unchanged; both
  forms are accepted.

## 2. Use semconv first

- **[checked]** If semconv defines the metric, use it. No `api_latency_ms` when
  `http.server.request.duration` exists. [`missing_metric`](rules.md#missing_metric)
- Domain metrics that semconv doesn't cover (`orders.created`, `payments.failed`) are declared in
  the project registry, like domain log fields. Their names are lowercase, dot-separated, and
  start with your own namespace, never a semconv one (`http.`, `db.`, `rpc.`, …).
- Don't rename a semconv metric to fit a dashboard. Fix the dashboard.

## 3. Units

- **[checked]** The unit of a semconv metric is the one semconv defines: durations in seconds
  (`s`), sizes in bytes (`By`). [`unit_mismatch`](rules.md#unit_mismatch)
- Units are [UCUM](https://ucum.org/) codes set as the instrument's unit. Counts of things use
  an annotation in braces: `{request}`, `{order}`. Ratios use `1`.
- **[v0.13.0]** The unit isn't part of the OTel name: no `_ms`, `_seconds`, `_bytes`, `_total`,
  `_count` in it (the Prometheus exporter adds suffixes). `adr_metric_name`

## 4. Instrument types

- **[checked]** A semconv metric uses the instrument semconv defines. [`unexpected_instrument`](rules.md#unexpected_instrument)
- Counter: a total that only goes up (requests, errors, bytes sent). UpDownCounter: a current
  amount that goes up and down (items in a queue, open connections). Histogram: durations and
  sizes, anything you want percentiles of. Gauge: a value read at a point in time (temperature,
  cache hit ratio).
- Never compute rates or averages in the service. Export the counter or histogram and let the
  query do it.

## 5. Attributes (labels)

The same rules as log fields ([standard §2](standard.md#2-field-names)), so the same thing has
the same name in a log line and on a metric.

- **[checked]** Semconv names, no synonyms: `http.response.status_code`, not `status`.
  [`adr_banned_synonym`](rules.md#adr_banned_synonym),
  [`adr_semconv_prefix`](rules.md#adr_semconv_prefix),
  [`missing_attribute`](rules.md#missing_attribute)
- **[checked]** Attributes that semconv requires on a semconv metric are present
  (`http.request.method` on `http.server.request.duration`). [`required_attribute_not_present`](rules.md#required_attribute_not_present)
- Use `http.route` (`/orders/{id}`), never `url.path` or `url.full`, on HTTP metrics.

## 6. Cardinality

Every distinct set of attribute values is a separate time series that costs memory and money.
This is the most common way metrics go wrong, and semconv alone doesn't prevent it: `user.id`
is a valid semconv attribute, but not as a metric label.

- **[checked]** No unbounded attributes on metrics: IDs (`user.id`, `session.id`, `*.id` of
  domain objects), emails, `url.full`, `url.path`, `url.query`, raw error messages,
  timestamps. [`adr_metric_cardinality`](rules.md#adr_metric_cardinality)
- **[checked]** Each metric stays within a series budget (1,000 series per metric per service
  by default, configurable per metric). Above that, the finding names the attribute with the
  most distinct values. [`adr_metric_cardinality`](rules.md#adr_metric_cardinality)
- If you need per-user or per-request detail, it belongs in logs or traces, not metrics.

## 7. Sensitive data

- **[checked]** No personal data or secrets in attribute values: the same patterns as for logs.
  [`adr_sensitive_data`](rules.md#adr_sensitive_data)

## 8. Resource identity

- **[v0.13.0]** Every metric carries `service.name`, `service.version` and
  `deployment.environment.name` as resource attributes, as every log line does.
  [`adr_missing_required`](rules.md#adr_missing_required)
- Instance identity (`service.instance.id`, `k8s.pod.name`) comes from the resource, not from a
  label you add by hand.

## 9. Minimum coverage

- **[v0.13.0]** A service can list the metrics it must emit (`[metrics] required` in the
  config); a run without them fails. `adr_metric_required`
- HTTP and RPC servers emit request rate, errors and duration (the RED method); semconv's
  `http.server.request.duration` with `error.type` covers all three. Queue consumers emit lag
  and processing duration. Resources (pools, queues, caches) emit utilization, saturation and
  errors (the USE method).

## Checking metrics

**Experimental:** rule ids, messages and options for metrics may still change.

```bash
curl -s localhost:8000/metrics > metrics.txt     # or the registry dumped at the end of the tests
logwarden check --signal metrics metrics.txt
```

```text
logwarden (metrics, experimental): 5 metrics, 8 series, 10 findings; 4 library metrics not checked
  by rule: missing_metric=5, adr_banned_synonym=3, missing_attribute=2
...
  http_requests_total {method}	adr_banned_synonym	§2: 'method' is a banned synonym; use http.request.method (3 series)
  http_requests_total	missing_metric	Metric does not exist in the registry. Semconv has http.server.request.duration for this: use it (exported as http_server_request_duration_seconds). (3 series)
```

- **Names.** Each Prometheus name is translated back to its OTel name the way the OTel
  exporter derives it: `_total` is dropped from counters, a unit suffix becomes the unit
  (`_seconds` → `s`, `_bytes` → `By`, `_ratio` → `1`), `_` matches `.` in a registry name.
  Dotted names from Prometheus 3 (`{"http.server.request.duration"}`) are read as they are.
  `# TYPE` gives the instrument (a gauge matches an UpDownCounter, a summary a histogram).
  Labels map to attributes the same way (`http_route` → `http.route`).
- **One finding per metric, label and rule,** with the number of series it affects.
- **Not checked:** the metrics client libraries export on their own (`process_*`, `python_*`,
  `go_*`, `jvm_*`, `nodejs_*`, `promhttp_*`), and the `_created` series of `prometheus_client`.
- **Domain metrics** are declared in the same fields file as domain log fields, as a group with
  `type: metric`:

  ```yaml
  groups:
    - id: metric.shop.orders
      type: metric
      metric_name: shop.orders
      brief: Orders placed.
      instrument: counter
      unit: "{order}"
      stability: development
      attributes:
        - ref: shop.order.kind
  ```

- `--series-budget N`: series per metric before `adr_metric_cardinality` (default 1000).
- Outputs: the text summary, `--json`, `--markdown FILE`. Exit codes as for logs. The config's
  `fields` and `disable` apply; baseline, `--compare`, SARIF, Code Quality and HTML are logs
  only for now (planned for v0.13.0).
- Reports never contain label values, only metric names, label names and rule messages.
