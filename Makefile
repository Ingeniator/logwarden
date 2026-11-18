.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.PHONY: test
test:  ## Run the test suite (downloads weaver on first run)
	uv run pytest -q

.PHONY: golden
golden:  ## Regenerate tests/golden from the examples (review the diff before committing)
	-uv run logwarden check examples/good.jsonl --json > tests/golden/good.json
	-uv run logwarden check examples/good.jsonl --max 1000 > tests/golden/good.txt
	-uv run logwarden check examples/bad.jsonl --json > tests/golden/bad.json
	-uv run logwarden check examples/bad.jsonl --max 1000 > tests/golden/bad.txt
	-uv run logwarden check tests/data/mixed.jsonl --json > tests/golden/mixed.json
	-uv run logwarden check tests/data/mixed.jsonl --max 1000 > tests/golden/mixed.txt
	git diff --stat tests/golden

.PHONY: check
check:  ## Check a log file: make check LOGS=path/to/logs.jsonl
	@[ -n "$(LOGS)" ] || { echo "usage: make check LOGS=path/to/logs.jsonl"; exit 2; }
	uv run logwarden check "$(LOGS)"

.PHONY: build
build:  ## Build sdist and wheel into dist/
	uv build

.PHONY: validate-plugin
validate-plugin:  ## Validate the Claude Code marketplace and plugin manifests
	claude plugin validate --strict .
	claude plugin validate --strict plugin
