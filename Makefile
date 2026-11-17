.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.PHONY: install-weaver
install-weaver:  ## Download the pinned weaver binary into .tools/ (if not on PATH)
	@bin/logwarden setup

.PHONY: test
test: install-weaver  ## Run the smoke test against examples/
	./tests/smoke.sh

.PHONY: check
check:  ## Check a log file: make check LOGS=path/to/logs.jsonl
	@[ -n "$(LOGS)" ] || { echo "usage: make check LOGS=path/to/logs.jsonl"; exit 2; }
	bin/logwarden check "$(LOGS)"
