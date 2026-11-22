# Start here: GitHub

Check the logs your tests write, with a comment on every pull request that shows new and fixed
findings. About 10 minutes.

## 1. Start with the essential rules

Create `logwarden.toml` in the repository root:

```toml
profile = "essential"
```

This checks the three things every service should pass as it is: no secrets or personal data,
every line is JSON, and every line has `service.name`, `service.version` and
`deployment.environment.name`.

## 2. Save the test logs

The check reads a file of JSON log lines from your tests.

- **Python with pytest:** install `logwarden` with the test dependencies (the workflow below
  does) and run `LOGWARDEN_CAPTURE=logs.jsonl pytest`. The plugin writes what your own logging
  setup produces. Add `logs.jsonl` to `.gitignore`.
- **Anything else:** have the service log JSON to a file during the tests, e.g. by pointing its
  log output at `logs.jsonl`. The [setup recipes](../recipes/node.md) show it for Node, Go and Java.

## 3. Add the workflow

`.github/workflows/logwarden.yml`:

```yaml
name: logwarden
on:
  push:
    branches: [main]
  pull_request:
permissions:
  contents: read
  pull-requests: write          # for the PR comment
jobs:
  logs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt logwarden==0.10.1 pytest
      - run: LOGWARDEN_CAPTURE=logs.jsonl pytest
      - uses: Ingeniator/logwarden@v0.10.1
        with:
          logs: logs.jsonl
          comment: "true"
          fail: "false"         # report only, until you're ready to block
```

Replace the install and test steps with your own. Commit and push to `main`: the run on `main`
records the snapshot that pull requests are compared with. Open the run (*Actions* tab, or
`gh run view --web`): its **Summary** page and the `logwarden-report` artifact
(`gh run download`) start with **Fix first**: the places that cause most of the violations.

## 4. Open a pull request

The pull request gets one comment, updated on each push, with the new and fixed findings
compared with `main`. A finding is identified by rule, field, logger and event, so a new
problem in a new place is *new*, while one more line with a problem `main` already has counts as
*unchanged*.

## Next

- Fix the top places in **Fix first**.
- When `essential` is clean, move to the full standard in one reviewed change:
  `uvx logwarden init --from logs.jsonl` declares your service's own fields in
  `logwarden-fields.yaml` (names and types, no values), lists the fields to rename instead,
  and sets `profile = "standard"`. Review the file, commit it with `logwarden.toml`. Then remove
  `fail: "false"` to block new violations.
- Findings on the source line in the diff (SARIF), baselines and more: the
  [integration guide](../integration.md#3-check-in-ci).
