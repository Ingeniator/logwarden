"""Fixture: a module the run never imports."""
import logging

log = logging.getLogger(__name__)


def run():
    log.error("unused_failed")                                               # mark:unreached
