"""Fixture run (stands in for a test suite): logs as JSON lines with the callsite fields."""
from app.svc import Svc

from logwarden.logging import configure

configure("logcov-fixture", "1", "test", level="INFO")
s = Svc()
s.ok()
s.fail()
