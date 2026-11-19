"""Log through the OpenTelemetry SDK's OTLP/HTTP exporter, configured only by OTEL_* env vars.

usage: python otlp_emit.py good|bad [exit_code]
"""
import logging
import sys

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

mode = sys.argv[1]
resource = Resource.create({"service.name": "otlp-demo", "service.version": "1.0.0",
                            "deployment.environment.name": "test" if mode == "good" else "dev"})
provider = LoggerProvider(resource=resource)
provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
set_logger_provider(provider)
trace.set_tracer_provider(TracerProvider(resource=resource))

log = logging.getLogger("otlp_demo")
log.propagate = False
log.setLevel(logging.INFO)
log.addHandler(LoggingHandler(logger_provider=provider))

with trace.get_tracer("otlp_demo").start_as_current_span("work"):
    if mode == "good":
        log.info("upstream_call_completed", extra={"server.address": "api.example.com"})
        try:
            raise TimeoutError("upstream timed out")
        except TimeoutError:
            log.warning("upstream_call_failed", exc_info=True, extra={"server.address": "api.example.com"})
    else:
        log.info("Imported 3 rows", extra={"status": 200})
provider.shutdown()
sys.exit(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
