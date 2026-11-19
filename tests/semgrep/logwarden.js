const pino = require('pino');
const log = pino();
const n = 3, user = 'u', pw = 'x';

// ruleid: logwarden-event-name-js
log.info(`Imported ${n} rows`);
// ruleid: logwarden-event-name-js
log.info({ rows_imported: n }, `imported ${n}`);
// ruleid: logwarden-event-name-js
log.error('failed for ' + user);
// ruleid: logwarden-event-name-js
log.warn({ order_id: 1 }, 'Order not found');
// ok: logwarden-event-name-js
log.info({ rows_imported: n }, 'import_completed');
// ok: logwarden-event-name-js
log.info('import_completed');

// ruleid: logwarden-sensitive-key-js
log.info({ password: pw }, 'login_attempted');
// ruleid: logwarden-sensitive-key-js
log.child({ 'api_key': pw });
// ok: logwarden-sensitive-key-js
log.info({ user_id: user }, 'login_attempted');

// ruleid: logwarden-banned-key-js
log.error({ err: new Error('x') }, 'upstream_call_failed');
// ok: logwarden-banned-key-js
log.error({ 'exception.type': 'Error' }, 'upstream_call_failed');

// ruleid: logwarden-print-js
console.log('starting');
// ok: logwarden-print-js
log.info('service_started');
