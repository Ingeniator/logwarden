import logging
import sys

import structlog

log = structlog.get_logger()
logger = logging.getLogger(__name__)
n, user, err, pw = 3, "u", ValueError("x"), "hunter2"

# ruleid: logwarden-event-name-python
log.info(f"Imported {n} rows")
# ruleid: logwarden-event-name-python
logger.warning("rows %d" % n)
# ruleid: logwarden-event-name-python
logger.error("failed for {}".format(user))
# ruleid: logwarden-event-name-python
log.info("import done: " + user)
# ruleid: logwarden-event-name-python
logger.info("Import completed", extra={"rows_imported": n})
# ruleid: logwarden-event-name-python
logger.info("rows %d", n)
# ok: logwarden-event-name-python
log.info("import_completed", rows_imported=n)
# ok: logwarden-event-name-python
logger.exception("import_failed")
# ok: logwarden-event-name-python
parser_error = argparse_parser.error(f"bad {n}")

# ruleid: logwarden-sensitive-key-python
log.info("login_attempted", password=pw)
# ruleid: logwarden-sensitive-key-python
logger.info("call_made", extra={"api_key": pw})
# ruleid: logwarden-sensitive-key-python
log.bind(auth_token=pw)
# ruleid: logwarden-sensitive-key-python
log.info("call_made", **{"http.request.header.authorization": pw})
# ok: logwarden-sensitive-key-python
log.info("login_attempted", user_id=user, token_present=True)

# ruleid: logwarden-banned-key-python
log.error("upstream_call_failed", error=str(err))
# ruleid: logwarden-banned-key-python
logger.info("request_done", extra={"status": 200})
# ok: logwarden-banned-key-python
log.info("request_done", **{"http.response.status_code": 200})

# ruleid: logwarden-print-python
print("starting")
# ok: logwarden-print-python
log.info("service_started")
# ok: logwarden-print-python
print("progress", file=sys.stderr)
