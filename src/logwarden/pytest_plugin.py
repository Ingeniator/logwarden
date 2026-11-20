"""pytest plugin: copy every log line of the test run to $LOGWARDEN_CAPTURE.

Installed via pip, the plugin loads automatically and does nothing unless
LOGWARDEN_CAPTURE is set:  LOGWARDEN_CAPTURE=logs.jsonl pytest
Then:  logwarden check logs.jsonl

While capturing it also sets LOGWARDEN_CALLSITE=1 (unless set), so logwarden.logging.configure()
adds where each line was logged (code.file.path, code.line.number) for `check --sarif`.

LOGWARDEN_LEVEL (default INFO) is the lowest level captured. The root logger is
lowered to it if needed, because under pytest an app's logging.basicConfig() is
often a no-op (pytest already installed handlers) and the level stays WARNING.
"""
import logging
import os

import pytest

_handler = None


def pytest_configure(config):
    if os.getenv("LOGWARDEN_CAPTURE"):
        os.environ.setdefault("LOGWARDEN_CALLSITE", "1")


def _service_formatter():
    """The formatter of the app's own root handler (not pytest's), else the raw message."""
    for h in logging.getLogger().handlers:
        if h is not _handler and not type(h).__module__.startswith("_pytest") and h.formatter:
            return h.formatter
    return logging.Formatter("%(message)s")


@pytest.fixture(autouse=True)
def _logwarden_capture():
    global _handler
    path = os.getenv("LOGWARDEN_CAPTURE")
    if path:
        root = logging.getLogger()
        if _handler is None:
            _handler = logging.FileHandler(path, mode="w", encoding="utf-8")
        # Re-attach before every test: apps often call basicConfig(force=True) or reset handlers.
        _handler.setFormatter(_service_formatter())
        if _handler not in root.handlers:
            root.addHandler(_handler)
        level = logging.getLevelName(os.getenv("LOGWARDEN_LEVEL", "INFO").upper())
        if root.getEffectiveLevel() > level:
            root.setLevel(level)
    yield


def pytest_sessionfinish(session, exitstatus):
    if _handler is not None:
        logging.getLogger().removeHandler(_handler)
        _handler.close()
