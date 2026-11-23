# AGENTS.md: working on logwarden

Instructions for AI agents (and humans) changing this repo. To *use* logwarden in another
project, see `plugin/skills/logwarden/SKILL.md` and `docs/integration.md` instead.

## What this repo is

A guardrail for structured logs, published as the PyPI package `logwarden`.
`logwarden check` converts JSON log lines into OTel Weaver samples (`check.to_sample`), runs
`weaver registry live-check` with the registry and Rego rules in `src/logwarden/_data/`,
then summarises the result (`check.summarise`) with the fix hints from `rules.json`, after
applying the project's `disable` list and baseline (`check.Policy`).

| Path | Role |
|---|---|
| `src/logwarden/cli.py` | CLI: `check`, `baseline`, `capture`, `catalog`, `fetch`, `lint-code`, `rules`, `init`, `setup`, `mcp`, `version`. Exit codes 0 / 1 / 2 |
| `src/logwarden/check.py` | Line parsing, Weaver sample conversion, running Weaver, rules that need the raw line (`adr_non_json_line`), profiles (`PROFILES`), `disable` and baseline, "Fix first" (`fix_first`), the result dict; the registry chain logwarden → GenAI → semconv (`base_registry`, `base_chain`: built once in the cache per pinned versions, `CHAIN_FORMAT` bumps it) |
| `src/logwarden/weaver.py` | Finds Weaver, or downloads the pinned release and checks its SHA-256; downloads and caches the semconv and GenAI semconv release archives (pinned SHA-256 in `SEMCONV_SHA256`, `GENAI_SHA256`) |
| `src/logwarden/report.py` | Markdown summary (PR comment, under GitHub's size limit), the self-contained HTML report, Prometheus metrics (`--metrics`) |
| `src/logwarden/sources.py` | Input formats (JSON lines, Elasticsearch `_search`, Kibana CSV with registry types) and `[source]` unwrapping (Fluent Bit `log`, `ignore_fields`), service of each record |
| `src/logwarden/sarif.py` | SARIF 2.1.0 for `check --sarif` (location from `code.file.path`/`code.line.number`) and `lint-code --sarif`; schema in `tests/data/` |
| `src/logwarden/codequality.py`, `ci/gitlab/logwarden.gitlab-ci.yml` | GitLab Code Quality report (`--codequality`, same locations and fingerprints as SARIF, one fingerprint per finding) and the GitLab CI template (Docker image, offline, allowed failure on exit 1; never `-o`, whose work files hold logged values) |
| `src/logwarden/selflog.py` | logwarden's own messages on stderr: plain text, or JSON records that pass the standard (`LOGWARDEN_LOG_FORMAT=json`, `--log-format`; default in the Docker image). `cli._err`/`_info` call it with a constant event name; `logwarden_detail` is declared in the registry. `tests/test_selflog.py` checks logwarden's own output with `--profile standard` |
| `src/logwarden/fetch.py` | `logwarden fetch` (newest lines of a window from Elasticsearch; credentials only from the environment) and the Pushgateway push (`check --pushgateway`) |
| `integrations/grafana/logwarden.json` | Grafana dashboard for the pushed metrics (scheduled checks) |
| `src/logwarden/fields.py` | `init --from`: proposes the fields file from a `standard` run (`check.declarable`, `check.NOT_DECLARED`), appends without a YAML library, sets `profile` |
| `scripts/update_pins.py`, `.github/workflows/update-pins.yml` | Weekly pin update: new Weaver, semconv and GenAI semconv versions with the archive SHA-256, tests, a PR |
| `src/logwarden/catalog.py` | Event catalog: events of a run, reading and merging `logwarden-events.toml` |
| `src/logwarden/capture.py` | OTLP/HTTP receiver (`logwarden capture`): hand-written protobuf and JSON decoding, record → JSON line |
| `src/logwarden/lint.py`, `src/logwarden/_data/semgrep/logwarden.yml` | `logwarden lint-code`: runs the Semgrep rules (one per language for each check); fixtures in `tests/semgrep/` |
| `src/logwarden/config.py` | `[tool.logwarden]` in `pyproject.toml`, or `logwarden.toml` (`fields`, `baseline`, `disable`, `upstreams`, `events`, `[source]`) |
| `src/logwarden/_data/policies/logwarden.rego` | Rules that run per log line (Rego, package `live_check_advice`) |
| `src/logwarden/_data/rules.json` | Rule id → section, title, hint, doc anchor |
| `src/logwarden/_data/registry/` | Weaver registry: the GenAI semconv dependency (which brings semconv) + shared domain fields + old GenAI names as deprecated (`genai-deprecated.yaml`). `check.base_registry` builds logwarden → GenAI → semconv without the directories GenAI replaces, so each field has one definition (`tests/test_genai.py`) |
| `src/logwarden/logcoverage.py` | `logwarden log-coverage`: finds Python log calls (`ast`), reads coverage.py data (`.coverage` SQLite, JSON, Cobertura XML) and captured lines; fixture project in `tests/data/logcov/` |
| `src/logwarden/logging.py` | `configure()` (the reference structlog setup) and the failure helpers; `[structlog]` extra |
| `src/logwarden/patterns.py` | Sensitive-data patterns in Python (redaction, `scrub`); mirror of `adr_sensitive_data` |
| `src/logwarden/testing.py` | `capture`, `assert_failure_logged`, `assert_logs_clean` |
| `src/logwarden/pytest_plugin.py` | Capture plugin (`LOGWARDEN_CAPTURE`), registered through the `pytest11` entry point |
| `src/logwarden/mcp_server.py` | MCP server (`logwarden mcp`, `[mcp]` extra) |
| `docs/start/` | The four "Start here" pages (Elasticsearch, GitLab, GitHub, agents): the recommended path for a new team. Keep them short and runnable word for word |
| `STABILITY.md` | What is public interface and how it changes; `tests/test_adoption.py` checks its rule ids and env vars |
| `docs/standard.md`, `docs/rules.md` | The standard and one section per rule. Copied into the wheel |
| `plugin/`, `.claude-plugin/marketplace.json` | Claude Code plugin (skill, MCP config, `/logwarden:check`) and its marketplace |
| `action.yml` | GitHub Action: runs logwarden from its own checkout with `uvx`; base snapshot through the Actions cache, PR comment, report artifact. CI jobs `action`, `review` and `review-readonly` exercise it |
| `Dockerfile` | Image `ghcr.io/ingeniator/logwarden`: Weaver and semconv downloaded at build time, so it works offline |
| `docs/recipes/`, `examples/recipes/` | Node, Go and Java logger setups; `examples/recipes/check.sh` runs each example and checks its output (CI job `recipes`) |
| `mkdocs.yml`, `docs_hooks.py`, `.github/workflows/pages.yml` | Docs site https://ingeniator.github.io/logwarden/ from `docs/`; the home page is `README.md`, release notes join the nav automatically. Build: `uvx --with mkdocs-material==9.5.50 --from mkdocs==1.6.1 mkdocs build` (strict: broken links fail) |
| `tests/golden/` | Reviewed full output for the example files. Regenerate only on purpose (`make golden`), review the diff, and explain it in the release notes |

## Commands

```bash
uv run pytest -q                    # all tests (real Weaver runs; downloads it on first use)
uv run coverage run -m pytest -q && uv run coverage combine && uv run coverage report   # CI fails under 85%
uv run logwarden check examples/bad.jsonl
uv build                            # wheel + sdist
make validate-plugin                # claude plugin validate --strict for marketplace + plugin
examples/recipes/check.sh           # run the Node/Go/Java examples and check their output
uv run logwarden lint-code examples/recipes src/logwarden   # Semgrep rules (uses uvx if needed)
docker build -t logwarden:dev .     # the image; try: docker run --rm --network none -v "$PWD/examples:/work" logwarden:dev check good.jsonl
```

`uv run pytest` must pass before you commit.

## Adding or changing a rule

Do all of these in one change:
1. Add the rule to `src/logwarden/_data/policies/logwarden.rego`. The message starts with the
   standard section (`"§4: ..."`) and says what to use instead.
2. Add an entry to `src/logwarden/_data/rules.json` (`section`, `title`, `hint`, `doc`).
3. Add `## <rule_id>` to `docs/rules.md` with a failing line, a passing line and the fix.
   The MCP tool `explain_rule` reads this heading, so the format matters.
4. Mark the rule **[checked]** in `docs/standard.md`.
5. Add a failing line to `examples/bad.jsonl` and tests with passing *and* failing cases
   (see `tests/test_new_rules.py`). Keep `examples/good.jsonl` passing. Changing the
   examples changes the goldens: run `make golden`, review the diff, and say why in the
   commit. `tests/test_rules_wired.py` fails if a rule lacks a `rules.json` entry, a
   `docs/rules.md` section, a link from `docs/standard.md`, or a line in `bad.jsonl`.
6. Add a row to the rule table in `README.md`, and the id to `STABILITY.md`.
7. Decide whether it belongs in the `essential` profile (`check.PROFILES`). Only rules that
   every service should pass as it is go there, and the list is a public interface.

Rego runs per sample (one log line or one attribute), so it can't check anything across
a run, such as "never seen", or lines that aren't JSON objects. Write those in Python in
`check.collect`. A Rego type error (e.g. `regex.match` on a number) aborts the whole Weaver
run, so guard every builtin with `is_string`/`is_number`. Messages must never contain the
logged value of a possibly sensitive field.

## Constraints

- The core install has no runtime dependencies (except `tomli` on Python 3.10). Optional
  features go in extras (`[mcp]`, `[structlog]`).
- `patterns.py` and the Rego `adr_sensitive_data` rule must agree: whatever `configure()`
  redacts must pass the rule (`test_claim2_redaction_leaves_nothing_for_the_rule`).
- The Semgrep rules mirror the runtime rules: the banned-key regex equals the Rego `banned`
  set, and the sensitive-key regex agrees with `patterns.SENSITIVE_KEY`
  (`tests/test_lint_code.py`). Every Semgrep rule needs `ruleid:` and `ok:` lines in
  `tests/semgrep/`; Semgrep's `metavariable-regex` is anchored at the start, so write
  `^(.*[._-])?name$`, not `(^|[._-])name$`.
- Semgrep is never a dependency: it pins old OpenTelemetry and `mcp` versions. `lint-code`
  runs it from PATH or with `uvx`.
- Reports never show logged values: only rule messages (which never contain them), event and
  field names. Escape everything in HTML and Markdown (`tests/test_review.py` logs
  `<script>` as an event name).
- Per-service numbers and metric labels contain service names and rule ids only. The metric
  names (`logwarden_lines_total`, `logwarden_lines_with_violations_total`,
  `logwarden_violations_total`) are a public interface, like rule ids.
- A finding's `line` is the line, CSV row or search hit number of its record; `file_line`
  (internal) is its line in the input file, used for SARIF.
- Snapshots use the baseline's entry format (`make_baseline`), so a baseline also works as
  `--compare`. `--compare` never changes the exit code.
- `capture` writes only the result to stdout; the wrapped command's stdout goes to stderr.
- `STABILITY.md` lists the public interface. Rule ids, the `--json` keys and exit codes are a public interface for CI configs, agent
  prompts and the Action. Only add, never rename. Add a new id and keep the old one if needed.
- Weaver 0.28 emits findings as `{id, level, message}`. `summarise` also accepts the older
  `advice_type`/`advice_level` names.
- Never print to stdout outside the result. MCP uses stdio, and `--json` must stay parseable.
- Messages go to stderr only through `cli._err`/`cli._info` (or `selflog.emit`), each with a
  constant snake_case event name, so logwarden's own output passes its standard in JSON mode.
  Never `print(..., file=sys.stderr)`.
- Weaver judges each sample on its own, so large inputs run in parallel chunks
  (`check.live_check`). A new Rego rule must not need other samples. Guard an expensive
  regular expression with a cheap `contains` on text every match must have (see the
  sensitive-data rules): Weaver's Rego engine runs it on every value.

## Releasing

Every version follows **notes first, verify last**:

1. **Before implementing**, write `docs/releases/vX.Y.Z.md` with status `Planned`.
   It covers what's new, breaking changes, upgrade steps, and a verification table that
   gives a concrete check for every claim (result `⏳`). Add a row to `CHANGELOG.md`.
   Plans come from `docs/roadmap.md`.
2. Implement. If the scope changes, update the notes first, so they always describe what
   will ship.
3. **Before tagging**, run every check in the table and record the result (`✅` plus
   evidence such as a CI run id or command output, or `❌` plus what failed). A `❌` blocks
   the release: fix it, or move the claim out of the notes.
4. Bump the version: `src/logwarden/__init__.py`, `plugin/.claude-plugin/plugin.json`,
   `.claude-plugin/marketplace.json` and the `logwarden[mcp]==` pin in `plugin/.mcp.json`
   (`tests/test_versions.py` checks they agree). Update the `@vX.Y.Z`, `==X.Y.Z`, image `:X.Y.Z`
   and pre-commit `rev: vX.Y.Z` references in `README.md`, `docs/integration.md` and the GitLab template (`tests/test_versions.py` checks), set the notes' status to `Released <date> ·
   tag vX.Y.Z` (post-release checks such as installing from PyPI are added after publishing), and update `CHANGELOG.md`.
5. Commit, wait for CI to pass, then `git tag -a vX.Y.Z -m "..."` and
   `git push origin vX.Y.Z`. The `release` workflow refuses a tag whose notes aren't
   marked Released. It publishes to PyPI with trusted publishing, pushes the Docker image
   (`:X.Y.Z` and `:latest`) to GHCR, and creates the GitHub release from the notes. Run the
   workflow manually for a dry run: TestPyPI and the image tag `:X.Y.Z.devN`.
