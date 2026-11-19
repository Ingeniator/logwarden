'use strict';
// Example service code: what logging looks like at call sites.
require('./tracing');
const { trace } = require('@opentelemetry/api');
const { createLogger, errorFields } = require('./log');

const log = createLogger({ serviceName: 'orders-api', serviceVersion: '1.0.0', environment: 'test' })
  .child({ logger: 'orders' });

trace.getTracer('orders').startActiveSpan('create_order', (span) => {
  log.info({ order_id: 42 }, 'order_created');
  log.debug({ order_id: 42 }, 'order_cached');
  try {
    throw new TypeError('payment provider returned HTML');
  } catch (err) {
    log.error({ order_id: 42, 'server.address': 'payments.example.com', ...errorFields(err) },
              'payment_call_failed');
  }
  log.warn({ 'error.type': 'timeout', 'server.address': 'payments.example.com' }, 'payment_call_retried');
  span.end();
});
