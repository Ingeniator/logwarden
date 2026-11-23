"""Fixture for tests/test_log_coverage.py: log calls the run reaches or not."""
import logging

import structlog

log = logging.getLogger(__name__)


class Svc:
    def __init__(self):
        self._logger = logging.getLogger("app.svc")

    def ok(self):
        self._logger.info("svc_ok_completed")                                # mark:reached-emitted
        log.debug("svc_debug_written")                                       # mark:reached-silent
        structlog.get_logger().bind(step=1).info("svc_bound_completed")      # mark:reached-emitted
        log.log(logging.WARNING, "svc_level_arg_completed")                  # mark:reached-emitted
        result = compute(
            1,
            log.info("svc_inner_completed"),                                 # mark:reached-emitted
        )
        return result

    def fail(self):
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("svc_fail_failed")                                 # mark:reached-emitted
            logging.error("svc_root_failed")                                 # mark:reached-emitted

    def never(self):
        self._logger.warning("svc_never_skipped")                            # mark:unreached
        log.critical("svc_never_failed")                                     # mark:unreached

    def excluded(self):  # pragma: no cover
        log.error("svc_excluded_failed")


def compute(a, b):
    return a


def not_logs(dialog, catalog, warnings, obj):
    dialog.info("x")
    catalog.error("y")
    warnings.warn("z")
    obj.info("w")
    print("p")
