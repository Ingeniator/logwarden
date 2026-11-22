# Metrics standard

**Status: draft.** logwarden doesn't check metrics yet. Rules marked **[v0.10.0]** are planned
for [v0.10.0](releases/v0.10.0.md), an experiment on real services that decides whether
metrics checks are worth building out. Rules marked **[v0.11.0]** follow in
[v0.11.0](releases/v0.11.0.md) only if it shows they are. The rest is for code review. Rule ids link to
[rules.md](rules.md) where the rule already exists for logs.

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
- **[v0.10.0]** A scraped Prometheus name must be the translation of a registered OTel name:
  dots become `_`, the unit becomes a suffix (`s` → `_seconds`, `By` → `_bytes`, `1` → `_ratio`),
  and counters get `_total`. Prometheus 3 can also keep the dotted names unchanged; both
  forms are accepted.

## 2. Use semconv first

- **[v0.10.0]** If semconv defines the metric, use it. No `api_latency_ms` when
  `http.server.request.duration` exists. `missing_metric`
- Domain metrics that semconv doesn't cover (`orders.created`, `payments.failed`) are declared in
  the project registry, like domain log fields. Their names are lowercase, dot-separated, and
  start with your own namespace, never a semconv one (`http.`, `db.`, `rpc.`, …).
- Don't rename a semconv metric to fit a dashboard. Fix the dashboard.

## 3. Units

- **[v0.10.0]** The unit of a semconv metric is the one semconv defines: durations in seconds
  (`s`), sizes in bytes (`By`). `unit_mismatch`
- Units are [UCUM](https://ucum.org/) codes set as the instrument's unit. Counts of things use
  an annotation in braces: `{request}`, `{order}`. Ratios use `1`.
- **[v0.11.0]** The unit isn't part of the OTel name: no `_ms`, `_seconds`, `_bytes`, `_total`,
  `_count` in it (the Prometheus exporter adds suffixes). `adr_metric_name`

## 4. Instrument types

- **[v0.10.0]** A semconv metric uses the instrument semconv defines. `unexpected_instrument`
- Counter: a total that only goes up (requests, errors, bytes sent). UpDownCounter: a current
  amount that goes up and down (items in a queue, open connections). Histogram: durations and
  sizes, anything you want percentiles of. Gauge: a value read at a point in time (temperature,
  cache hit ratio).
- Never compute rates or averages in the service. Export the counter or histogram and let the
  query do it.

## 5. Attributes (labels)

The same rules as log fields ([standard §2](standard.md#2-field-names)), so the same thing has
the same name in a log line and on a metric.

- **[v0.10.0]** Semconv names, no synonyms: `http.response.status_code`, not `status`.
  [`adr_banned_synonym`](rules.md#adr_banned_synonym),
  [`adr_semconv_prefix`](rules.md#adr_semconv_prefix),
  [`missing_attribute`](rules.md#missing_attribute)
- **[v0.10.0]** Attributes that semconv requires on a semconv metric are present
  (`http.request.method` on `http.server.request.duration`). `required_attribute_not_present`
- Use `http.route` (`/orders/{id}`), never `url.path` or `url.full`, on HTTP metrics.

## 6. Cardinality

Every distinct set of attribute values is a separate time series that costs memory and money.
This is the most common way metrics go wrong, and semconv alone doesn't prevent it: `user.id`
is a valid semconv attribute, but not as a metric label.

- **[v0.10.0]** No unbounded attributes on metrics: IDs (`user.id`, `session.id`, `*.id` of
  domain objects), emails, `url.full`, `url.path`, `url.query`, raw error messages,
  timestamps. `adr_metric_cardinality`
- **[v0.10.0]** Each metric stays within a series budget (1,000 series per metric per service
  by default, configurable per metric). Above that, the finding names the attribute with the
  most distinct values. `adr_metric_cardinality`
- If you need per-user or per-request detail, it belongs in logs or traces, not metrics.

## 7. Sensitive data

- **[v0.10.0]** No personal data or secrets in attribute values: the same patterns as for logs.
  [`adr_sensitive_data`](rules.md#adr_sensitive_data)

## 8. Resource identity

- **[v0.11.0]** Every metric carries `service.name`, `service.version` and
  `deployment.environment.name` as resource attributes, as every log line does.
  [`adr_missing_required`](rules.md#adr_missing_required)
- Instance identity (`service.instance.id`, `k8s.pod.name`) comes from the resource, not from a
  label you add by hand.

## 9. Minimum coverage

- **[v0.11.0]** A service can list the metrics it must emit (`[metrics] required` in the
  config); a run without them fails. `adr_metric_required`
- HTTP and RPC servers emit request rate, errors and duration (the RED method); semconv's
  `http.server.request.duration` with `error.type` covers all three. Queue consumers emit lag
  and processing duration. Resources (pools, queues, caches) emit utilization, saturation and
  errors (the USE method).
