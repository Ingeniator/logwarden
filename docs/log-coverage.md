# Log coverage

`logwarden check` checks the log lines your tests write. A log call that no test reaches is
never checked, and these are usually the calls that matter most: the ones on error paths.
`logwarden log-coverage` shows which log calls the tests run, per file and level, from the
coverage data your test run already produces. Python source only for now.

```bash
coverage run -m pytest          # or: pytest --cov=src --cov-report=xml
logwarden log-coverage --coverage .coverage src/
```

```text
log-coverage: 20 of 55 log calls reached by the tests (36.4% reached)
  by level: debug 5/6 (83%), info 10/21 (48%), warning 5/8 (62%), error 0/16 (0%), exception 0/4 (0%)
  most unreached: src/app/storage.py 12/12; src/app/pipeline.py 6/7; src/app/upload.py 6/9

not reached (first 35 of 35):
  src/app/storage.py:110  error  logger
  ...
```

## What counts

- **A log call** is `<receiver>.<level>(...)`. The level is one of `debug`, `info`, `warning`,
  `warn`, `error`, `exception`, `critical`, `fatal`, `msg` or `log`. The receiver must
  contain `log`, `logger`, `logging`, `structlog` or `slog` as a word: `log`, `logger`,
  `self._logger`, `app.logger`, `logging`, `structlog.get_logger().bind(user_id=1)`. It doesn't
  match `dialog`, `catalog`, `self` or `warnings`. `logger.log(logging.ERROR, ...)` counts as
  `error`. If your loggers have other names, set `--receiver REGEX`.
- **Reached** means the coverage data says the statement containing the call ran. For a call
  inside a longer statement, such as `x = f(1, log.info(...))`, that's the statement's first line.
- **Not counted:**
  - test code: `tests/`, `test_*.py`, `*_test.py`, `conftest.py`;
  - `.venv`, `venv`, `node_modules`, `build`, `dist`, `migrations`;
  - calls excluded with `# pragma: no cover`, on the line itself or on the block that contains it;
  - files matching `--exclude GLOB`.
- **A file missing from the coverage data** (never imported by the tests) counts as not reached.

## Coverage data

Any of these, read without extra dependencies:

| File | Made by |
|---|---|
| `.coverage` | `coverage run`, `pytest --cov` (the default data file) |
| `coverage.json` | `coverage json` |
| `coverage.xml` (Cobertura) | `coverage xml`, `pytest --cov-report=xml` |

Paths are matched by their trailing parts, so data recorded in a CI checkout
(`/builds/team/app/src/x.py`) matches `src/x.py` in yours.

## Calls that wrote a line

A call can run and write nothing, for example a `debug` call below the log level. To count only
the calls that produced a line, also pass the lines captured from the tests. They need the
callsite fields `code.file.path` and `code.line.number`, which logwarden's pytest plugin turns
on:

```bash
LOGWARDEN_CAPTURE=logs.jsonl coverage run -m pytest
logwarden log-coverage --coverage .coverage --logs logs.jsonl src/
```

With `--logs`, the result also has `emitted`, and the percentage and the gate use it. `--logs`
works without `--coverage`; then only the emitted calls are reported.

## In CI

There's no gate by default; the command exits 0. To require a share:

```bash
# every error path is tested at least 80%
logwarden log-coverage --coverage .coverage --levels error,exception,critical --fail-under 80 src/
```

- `--fail-under PCT`: exit 1 when fewer than PCT% of the counted calls are reached (or wrote a
  line, with `--logs`).
- `--levels`: count only these levels.
- `--markdown FILE`: write a summary for a job summary (`$GITHUB_STEP_SUMMARY`) or a PR comment.
- `--json`: the full result, with these keys: `ok`, `measure` (`reached` or `emitted`), `calls`,
  `reached`, `emitted`, `percent`, `fail_under`, `levels`, `excluded_by_pragma`, `by_level`,
  `files`, `unreached`, `not_emitted`.

Exit codes are the same as for `check`: `0` OK, `1` below `--fail-under`, `2` usage error
(no data, unreadable file, unknown level).
