"""Spike: emit good and bad metrics over OTLP gRPC to weaver live-check."""
import sys
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

port = sys.argv[1]
reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"localhost:{port}", insecure=True),
                                       export_interval_millis=60_000)
res = Resource.create({"service.name": "orders", "service.version": "1.0.0",
                       "deployment.environment.name": "production"})
mp = MeterProvider(resource=res, metric_readers=[reader])
m = mp.get_meter("spike")

# 1 good: semconv metric, right unit and attributes
m.create_histogram("http.server.request.duration", unit="s").record(
    0.12, {"http.request.method": "GET", "http.response.status_code": 200, "http.route": "/orders/{id}",
           "url.scheme": "https"})
# 2 custom name where semconv has one
m.create_histogram("api_latency_ms", unit="ms").record(120, {"route": "/orders/42"})
# 3 semconv name, wrong unit
m.create_histogram("http.client.request.duration", unit="ms").record(
    120, {"http.request.method": "GET", "server.address": "api", "server.port": 443})
# 4 semconv name, wrong instrument type (counter instead of histogram)
m.create_counter("db.client.operation.duration", unit="s").add(1, {"db.system.name": "postgresql"})
# 5 banned synonym label + unbounded id label + email in a label
c = m.create_counter("orders.created", unit="{order}")
for i in range(300):
    c.add(1, {"status": "ok", "user.id": f"u{i}", "customer": f"user{i}@example.com"})
# 6 unit in the name, _total suffix
m.create_counter("payments_total_count", unit="1").add(3)
mp.shutdown()
