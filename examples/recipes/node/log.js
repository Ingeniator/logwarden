'use strict';
// pino set up to the logwarden logging standard. Copy this file into your service.
// https://github.com/Ingeniator/logwarden/blob/main/docs/recipes/node.md
const pino = require('pino');
const { trace, isSpanContextValid } = require('@opentelemetry/api');

const ENVIRONMENTS = new Set(['development', 'staging', 'test', 'production']);
const LEVELS = { warn: 'warning', fatal: 'critical' };   // pino's labels -> the standard's

function createLogger({
  serviceName = process.env.OTEL_SERVICE_NAME,
  serviceVersion = process.env.SERVICE_VERSION,
  environment = process.env.DEPLOYMENT_ENVIRONMENT,
  level = process.env.LOG_LEVEL || 'info',
} = {}) {
  if (!serviceName || !serviceVersion) throw new Error('logging: service name and version are required');
  if (!ENVIRONMENTS.has(environment)) {
    throw new Error(`logging: environment '${environment}' must be one of ${[...ENVIRONMENTS].join(', ')}`);
  }
  return pino({
    level,
    messageKey: 'event',                                       // log.info({...}, 'order_created')
    timestamp: () => `,"timestamp":"${new Date().toISOString()}"`,
    base: {                                                    // replaces pid and hostname
      'service.name': serviceName,
      'service.version': serviceVersion,
      'deployment.environment.name': environment,
    },
    formatters: { level: (label) => ({ level: LEVELS[label] || label }) },
    mixin() {                                                  // W3C ids of the active span
      const ctx = trace.getActiveSpan()?.spanContext();
      return ctx && isSpanContextValid(ctx) ? { trace_id: ctx.traceId, span_id: ctx.spanId } : {};
    },
    redact: {
      paths: ['password', 'token', 'api_key', 'authorization', 'cookie', 'secret',
              '*.password', '*.token', '*.api_key', '*.authorization', '*.cookie', '*.secret'],
      censor: '[REDACTED]',
    },
  });
}

// Fields for a caught error. Don't log it under `err` or `error` (banned synonyms).
function errorFields(err) {
  return {
    'exception.type': err?.name || 'Error',
    'exception.message': String(err?.message ?? err),
    'exception.stacktrace': err?.stack,
  };
}

module.exports = { createLogger, errorFields };
