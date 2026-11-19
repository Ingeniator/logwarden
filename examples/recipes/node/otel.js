'use strict';
// OTLP route: send pino's records to an OpenTelemetry collector (or `logwarden capture`)
// instead of, or as well as, stdout. Load before the app: node --require ./otel.js app.js
// The endpoint comes from OTEL_EXPORTER_OTLP_ENDPOINT (logwarden capture sets it).
const { logs } = require('@opentelemetry/api-logs');
const { OTLPLogExporter } = require('@opentelemetry/exporter-logs-otlp-http');
const { registerInstrumentations } = require('@opentelemetry/instrumentation');
const { PinoInstrumentation } = require('@opentelemetry/instrumentation-pino');
const { resourceFromAttributes } = require('@opentelemetry/resources');
const { BatchLogRecordProcessor, LoggerProvider } = require('@opentelemetry/sdk-logs');

const provider = new LoggerProvider({
  resource: resourceFromAttributes({
    'service.name': process.env.OTEL_SERVICE_NAME,
    'service.version': process.env.SERVICE_VERSION,
    'deployment.environment.name': process.env.DEPLOYMENT_ENVIRONMENT,
  }),
  processors: [new BatchLogRecordProcessor({ exporter: new OTLPLogExporter() })],
});
logs.setGlobalLoggerProvider(provider);
// disableLogCorrelation: log.js already adds trace_id/span_id (the instrumentation would add trace_flags too).
registerInstrumentations({ instrumentations: [new PinoInstrumentation({ disableLogCorrelation: true })] });

process.once('beforeExit', () => provider.shutdown());   // flush before exit
