.DEFAULT_GOAL := help

WEAVER_VERSION ?= v0.28.0
WEAVER_DIR     ?= .tools
UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)
ifeq ($(UNAME_S),Darwin)
  WEAVER_TARGET := $(if $(filter arm64,$(UNAME_M)),aarch64,x86_64)-apple-darwin
else
  WEAVER_TARGET := $(if $(filter aarch64 arm64,$(UNAME_M)),aarch64,x86_64)-unknown-linux-gnu
endif
WEAVER_URL := https://github.com/open-telemetry/weaver/releases/download/$(WEAVER_VERSION)/weaver-$(WEAVER_TARGET).tar.xz
export WEAVER ?= $(CURDIR)/$(WEAVER_DIR)/weaver-$(WEAVER_TARGET)/weaver

.PHONY: help
help:  ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.PHONY: install-weaver
install-weaver:  ## Download the pinned weaver binary into .tools/
	@[ -x "$(WEAVER)" ] || { mkdir -p $(WEAVER_DIR) && curl -fsSL "$(WEAVER_URL)" | tar -xJ -C $(WEAVER_DIR); }
	@"$(WEAVER)" --version

.PHONY: test
test: install-weaver  ## Run the smoke test against examples/
	./tests/smoke.sh

.PHONY: check
check: install-weaver  ## Check a log file: make check LOGS=path/to/logs.jsonl
	@[ -n "$(LOGS)" ] || { echo "usage: make check LOGS=path/to/logs.jsonl"; exit 2; }
	./bin/logwarden check "$(LOGS)"
