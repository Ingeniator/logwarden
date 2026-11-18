"""Test helpers: capture the JSON lines a service writes and assert on them.

    from logwarden.testing import capture, assert_failure_logged, assert_logs_clean

    def test_upstream_timeout(client, httpx_mock):
        httpx_mock.add_exception(httpx.ReadTimeout("timeout"))
        with capture() as logs:
            client.post("/chat")
        assert_failure_logged(logs, "upstream_call_failed", **{"server.address": "api.example.com"})
        assert_logs_clean(logs)          # full logwarden check (downloads Weaver on first use)

capture() formats records with the service's own root-handler formatter (the one set up by
logwarden.logging.configure(), or any JSON formatter), so the records are exactly what
production would write.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

__all__ = ["capture", "assert_failure_logged", "assert_logs_clean", "FAILURE_KEYS"]

FAILURE_KEYS = ("exception.type", "error.type", "http.response.status_code")
_FAILURE_LEVELS = {"warning", "warn", "error", "critical", "fatal"}


class _Collector(logging.Handler):
    def __init__(self, formatter: logging.Formatter, into: list):
        super().__init__(logging.NOTSET)
        self.setFormatter(formatter)
        self.into = into

    def emit(self, record: logging.LogRecord) -> None:
        text = self.format(record)
        for line in text.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                obj = {"_raw": line}
            self.into.append(obj if isinstance(obj, dict) else {"_raw": line})


def _service_formatter() -> logging.Formatter:
    for h in logging.getLogger().handlers:
        if getattr(h, "_logwarden_handler", False) and h.formatter:
            return h.formatter
    for h in logging.getLogger().handlers:
        if not type(h).__module__.startswith("_pytest") and h.formatter:
            return h.formatter
    return logging.Formatter("%(message)s")


@contextmanager
def capture(level: int | str = logging.INFO) -> Iterator[list[dict]]:
    """Collect the log records written inside the block, as parsed JSON dicts.

    Lines that aren't JSON appear as {"_raw": "<text>"}, so assert_logs_clean reports them.
    """
    records: list[dict] = []
    root = logging.getLogger()
    handler = _Collector(_service_formatter(), records)
    old_level = root.level
    lvl = level if isinstance(level, int) else logging.getLevelName(str(level).upper())
    root.addHandler(handler)
    if root.getEffectiveLevel() > lvl:
        root.setLevel(lvl)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def _is_failure(r: dict) -> bool:
    return str(r.get("level", "")).lower() in _FAILURE_LEVELS or str(r.get("event", "")).endswith("_failed")


def _describe(records: list[dict]) -> str:
    return ", ".join(f"{r.get('level', '?')}:{r.get('event', r.get('_raw', '?'))}" for r in records) or "none"


def assert_failure_logged(records: list[dict], event: str | None = None, **fields: Any) -> dict:
    """Assert a failure line (warning/error, or *_failed) was logged, matching `event` and
    `fields`, and that it says what failed (exception.type, error.type or a status code).

    Dotted field names: assert_failure_logged(logs, "x_failed", **{"server.address": "api"}).
    Returns the matching record.
    """
    failures = [r for r in records if _is_failure(r)]
    matching = [r for r in failures
                if (event is None or r.get("event") == event)
                and all(r.get(k) == v for k, v in fields.items())]
    want = f"event={event!r}" if event else "any event"
    if fields:
        want += " with " + ", ".join(f"{k}={v!r}" for k, v in fields.items())
    assert matching, f"no failure line logged for {want}; logged: {_describe(records)}"
    for r in matching:
        if any(r.get(k) not in (None, "") for k in FAILURE_KEYS):
            return r
    raise AssertionError(
        f"failure line {matching[0].get('event')!r} doesn't say what failed: it needs one of "
        f"{', '.join(FAILURE_KEYS)} (log with exc_info=True, or set error.type). Record: {matching[0]}")


def assert_logs_clean(records: list[dict] | str | Path, *, fields: str | Path | None = None,
                      cwd: str | Path | None = None) -> dict:
    """Run the full logwarden check on captured records (or a log file) and fail with the
    findings if there are violations. Uses the project config in `cwd` (default: current
    directory), like `logwarden check`. Returns the result."""
    from logwarden import check, cli

    if isinstance(records, (str, Path)):
        text = Path(records).read_text(encoding="utf-8")
    else:
        text = "".join((r["_raw"] if set(r) == {"_raw"} else json.dumps(r, ensure_ascii=False)) + "\n"
                       for r in records)
    project = Path(cwd) if cwd else Path.cwd()
    resolved, policy = cli.resolve(project, fields=str(fields) if fields else None)
    # Whole-run upstream coverage is about a full test run, not one test's records.
    policy.upstreams = []
    result = check.run(text, fields=resolved, policy=policy)
    if not result["ok"]:
        lines = [f"  line {f['line']}: {f['rule']}: {f['message'].splitlines()[0]}"
                 for f in result["findings"][:20]]
        hints = [f"  {r['rule']}: {r['hint']}" for r in result["by_rule"]]
        raise AssertionError(f"logwarden: {result['violations']} violations\n" + "\n".join(lines)
                             + "\nfix:\n" + "\n".join(hints))
    return result
