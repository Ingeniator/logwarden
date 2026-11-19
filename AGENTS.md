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
| `src/logwarden/cli.py` | CLI: `check`, `baseline`, `capture`, `lint-code`, `rules`, `init`, `setup`, `mcp`, `version`. Exit codes 0 / 1 / 2 |
| `src/logwarden/check.py` | Line parsing, Weaver sample conversion, running Weaver, rules that need the raw line (`adr_non_json_line`), `disable` and baseline, the result dict |
| `src/logwarden/weaver.py` | Finds Weaver, or downloads the pinned release and checks its SHA-256; downloads and caches the semconv release archive (pinned SHA-256 in `SEMCONV_SHA256`) |
| `src/logwarden/capture.py` | OTLP/HTTP receiver (`logwarden capture`): hand-written protobuf and JSON decoding, record → JSON line |
| `src/logwarden/lint.py`, `src/logwarden/_data/semgrep/logwarden.yml` | `logwarden lint-code`: runs the Semgrep rules (one per language for each check); fixtures in `tests/semgrep/` |
| `src/logwarden/config.py` | `[tool.logwarden]` in `pyproject.toml`, or `logwarden.toml` (`fields`, `baseline`, `disable`) |
| `src/logwarden/_data/policies/logwarden.rego` | Rules that run per log line (Rego, package `live_check_advice`) |
| `src/logwarden/_data/rules.json` | Rule id → section, title, hint, doc anchor |
| `src/logwarden/_data/registry/` | Weaver registry: semconv dependency + shared domain fields |
| `src/logwarden/logging.py` | `configure()` (the reference structlog setup) and the failure helpers; `[structlog]` extra |
| `src/logwarden/patterns.py` | Sensitive-data patterns in Python (redaction, `scrub`); mirror of `adr_sensitive_data` |
| `src/logwarden/testing.py` | `capture`, `assert_failure_logged`, `assert_logs_clean` |
| `src/logwarden/pytest_plugin.py` | Capture plugin (`LOGWARDEN_CAPTURE`), registered through the `pytest11` entry point |
| `src/logwarden/mcp_server.py` | MCP server (`logwarden mcp`, `[mcp]` extra) |
| `docs/standard.md`, `docs/rules.md` | The standard and one section per rule. Copied into the wheel |
| `plugin/`, `.claude-plugin/marketplace.json` | Claude Code plugin (skill, MCP config, `/logwarden:check`) and its marketplace |
| `action.yml` | GitHub Action: runs logwarden from its own checkout with `uvx` |
| `Dockerfile` | Image `ghcr.io/ingeniator/logwarden`: Weaver and semconv downloaded at build time, so it works offline |
| `docs/recipes/`, `examples/recipes/` | Node, Go and Java logger setups; `examples/recipes/check.sh` runs each example and checks its output (CI job `recipes`) |
| `tests/golden/` | Reviewed full output for the example files. Regenerate only on purpose (`make golden`), review the diff, and explain it in the release notes |

## Commands

```bash
uv run pytest -q                    # all tests (real Weaver runs; downloads it on first use)
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
6. Add a row to the rule table in `README.md`.

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
- `capture` writes only the result to stdout; the wrapped command's stdout goes to stderr.
- Rule ids, the `--json` keys and exit codes are a public interface for CI configs, agent
  prompts and the Action. Only add, never rename. Add a new id and keep the old one if needed.
- Weaver 0.28 emits findings as `{id, level, message}`. `summarise` also accepts the older
  `advice_type`/`advice_level` names.
- Never print to stdout outside the result. MCP uses stdio, and `--json` must stay parseable.

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
   (`tests/test_versions.py` checks they agree). Update the `@vX.Y.Z`, `==X.Y.Z` and image `:X.Y.Z`
   references in `README.md` and `docs/integration.md` (`tests/test_versions.py` checks), set the notes' status to `Released <date> ·
   tag vX.Y.Z` (post-release checks such as installing from PyPI are added after publishing), and update `CHANGELOG.md`.
5. Commit, wait for CI to pass, then `git tag -a vX.Y.Z -m "..."` and
   `git push origin vX.Y.Z`. The `release` workflow refuses a tag whose notes aren't
   marked Released. It publishes to PyPI with trusted publishing, pushes the Docker image
   (`:X.Y.Z` and `:latest`) to GHCR, and creates the GitHub release from the notes. Run the
   workflow manually for a dry run: TestPyPI and the image tag `:X.Y.Z.devN`.
