# Start here: GitLab

Check the logs your tests write, and see new and fixed findings in every merge request. The job
runs logwarden's Docker image and needs no internet access. It reports and doesn't block.
About 10 minutes.

## 1. Make the image available

If your runners can pull from `ghcr.io`, skip this step. Otherwise mirror the image into your
registry (on a machine that can reach both):

```bash
docker pull ghcr.io/ingeniator/logwarden:0.10.0
docker tag ghcr.io/ingeniator/logwarden:0.10.0 registry.example.com/mirror/logwarden:0.10.0
docker push registry.example.com/mirror/logwarden:0.10.0
```

Runners without internet access also need GitLab's own helper image in a registry they can
reach, as for any other job.

## 2. Add the template

Save the template in your repository as `ci/logwarden.gitlab-ci.yml`:

```bash
mkdir -p ci
curl -fsSL -o ci/logwarden.gitlab-ci.yml \
  https://raw.githubusercontent.com/Ingeniator/logwarden/v0.10.0/ci/gitlab/logwarden.gitlab-ci.yml
```

And start with the `essential` rules: no secrets or personal data, every line is JSON, and the
required service fields. Create `logwarden.toml` next to `.gitlab-ci.yml`:

```toml
profile = "essential"
```

## 3. Save the test logs

The job checks a file of JSON log lines that your test job keeps as an artifact. Use your usual
test job and image; add one of these:

- **The service can log JSON to a file** (any language): point its log output at `logs.jsonl`
  during the tests. The [setup recipes](../recipes/node.md) show it for Node, Go and Java.

    ```yaml
    test:
      script:
        - LOG_FILE=logs.jsonl npm test      # however your service picks its log file
      artifacts:
        when: always
        paths: [logs.jsonl]
    ```

- **Python with pytest:** add `logwarden` to the test dependencies (it needs PyPI or your
  package mirror) and run the tests with `LOGWARDEN_CAPTURE`. The plugin writes what your own
  logging setup produces. Without the package installed, the variable does nothing and no
  file is written.

    ```yaml
    test:
      script:
        - LOGWARDEN_CAPTURE=logs.jsonl pytest
      artifacts:
        when: always
        paths: [logs.jsonl]
    ```

## 4. Add the job

In `.gitlab-ci.yml`:

```yaml
include:
  - local: ci/logwarden.gitlab-ci.yml

logwarden:
  extends: .logwarden
  needs: [{job: test, artifacts: true}]
  variables:
    LOGWARDEN_IMAGE: registry.example.com/mirror/logwarden:0.10.0   # your copy from step 1; delete the line to use ghcr.io
    LOGWARDEN_LOGS: logs.jsonl
```

Commit and push to the default branch. While there are violations the `logwarden` job shows
as failed (exit code 1, an allowed failure) and the pipeline passes with a warning; a broken
setup fails it (exit code 2). The job's artifacts hold `report.html` and `report.md`, which
start with **Fix first**: the places that cause most of the violations.

## 5. Open a merge request

Push a branch with a change and open a merge request; no more configuration is needed. Its
pipeline compares with the target branch's latest snapshot ("Compared with the base
`main-snapshot.json`"). The **Reports** tab lists new and
fixed findings (Code Quality, on every GitLab tier), and the merge request links the HTML report.

![Code Quality findings in a merge request](../img/gitlab-code-quality.png)

## Production logs every hour

If your logs are in Elasticsearch, the same template checks them on a schedule, without
touching the service:

```yaml
logwarden-production:
  extends: .logwarden-production
  rules: [{if: '$CI_PIPELINE_SOURCE == "schedule"'}]
  variables:
    LOGWARDEN_IMAGE: registry.example.com/mirror/logwarden:0.10.0
    LOGWARDEN_ES_URL: https://elasticsearch.example.com:9200
    LOGWARDEN_ES_INDEX: "logs-shop-*"
    LOGWARDEN_ARGS: "--message-field log"           # if the JSON line is in a `log` field
    # LOGWARDEN_SINCE: 1h                           # the window each run checks
    # LOGWARDEN_PUSHGATEWAY: http://pushgateway.example.com:9091
```

Add `LOGWARDEN_ES_AUTH` (`user:password` or `ApiKey …`) under *Settings → CI/CD → Variables*,
masked (older GitLab versions can't mask a value with `:`; use an API key there), and an
hourly schedule under *Build → Pipeline schedules*. Each run compares with the previous one
(kept in the job's cache) and uses the profile in your `logwarden.toml`. The fetched logs are deleted after the check.
See [Elasticsearch or Kibana](elasticsearch.md) for the details.

Scheduled pipelines also run your other jobs. To run only this one on the schedule, add
`rules: [{if: '$CI_PIPELINE_SOURCE != "schedule"'}]` to `test` and `logwarden`.

## Next

- Fix the top places in **Fix first**.
- When `essential` is clean, move to the full standard in one reviewed change:
  `uvx logwarden init --from logs.jsonl` declares your service's own fields in
  `logwarden-fields.yaml` (names and types, no values), lists the fields to rename instead,
  and sets `profile = "standard"`. Review the file, commit it with `logwarden.toml`. (the
  `logs.jsonl` artifact of the test job).
- More options: the [integration guide](../integration.md#gitlab-ci).
