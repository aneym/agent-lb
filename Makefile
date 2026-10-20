PYTEST_ARGS := -v -ra -o faulthandler_timeout=300 -o faulthandler_exit_on_timeout=true --timeout=180 --timeout-method=thread --durations=20
POSTGRES_TEST_DATABASE_URL ?=
CI_RUN_ID := $(shell date +%s)-$(shell echo $$$$)
CI_IMAGE ?= ghcr.io/aneym/agent-lb-ci:$(CI_RUN_ID)
CI_BASE_REF ?= origin/main
CI_HEAD_REF ?=
CI_EVENT_PATH ?=
PYTHON ?= .venv/bin/python
SHELL := /bin/bash

.PHONY: help
help:
	@printf '%s\n' \
	  'Common targets:' \
	  '  make lint                    factory-lint lint, format and type checks' \
	  '  make architecture-check      proxy architecture fitness ratchets' \
	  '  make test-floor              kept-floor pytest guards, same as CI' \
	  '  make package                 build and verify sdist/wheel' \
	  '  make startup-benchmark       record 5 isolated startup/ready samples' \
	  '  make ci-fast                 lint/type/floor/package' \
	  '  make ci                      full local CI gate'

.PHONY: frontend-install frontend-build
frontend-install:
	cd frontend && bun install --frozen-lockfile

frontend-build: frontend-install
	cd frontend && bun run build

.PHONY: lint architecture-check
lint:
	factory-lint check --changed

architecture-check:
	$(PYTHON) scripts/check_proxy_architecture.py

.PHONY: test-floor test-e2e
test-floor:
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/floor

test-e2e: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/e2e

.PHONY: migration-check migration-check-postgres
migration-check:
	uv sync --dev --frozen
	set -e; TMP_DB="$$(mktemp /tmp/agent-lb-ci-migrate-XXXXXX)"; \
	DB_URL="sqlite+aiosqlite:///$${TMP_DB}"; \
	trap 'rm -f "$${TMP_DB}"' EXIT; \
	uv run agent-lb-db --db-url "$${DB_URL}" upgrade head; \
	uv run agent-lb-db --db-url "$${DB_URL}" check

migration-check-postgres:
	@test -n "$(POSTGRES_TEST_DATABASE_URL)" || (echo "Set a disposable POSTGRES_TEST_DATABASE_URL"; exit 1)
	uv sync --dev --frozen
	uv run agent-lb-db --db-url "$(POSTGRES_TEST_DATABASE_URL)" upgrade head
	uv run agent-lb-db --db-url "$(POSTGRES_TEST_DATABASE_URL)" check

.PHONY: startup-benchmark
startup-benchmark:
	$(PYTHON) scripts/benchmark-startup.py service --samples 5 --label agent-lb-isolated

.PHONY: package
package: frontend-build
	uv sync --frozen --no-dev
	uv run python -c "import app; import app.main; print('import ok')"
	rm -rf build dist *.egg-info
	uvx --from build==1.3.0 python -m build
	$(PYTHON) scripts/verify-wheel-assets.py

.PHONY: docker
docker:
	docker build -t $(CI_IMAGE) .
	trivy image --format table --exit-code 1 --severity CRITICAL,HIGH --ignore-unfixed $(CI_IMAGE)

.PHONY: ci-fast ci ci-targets ci-doctor menubar-build
ci-fast: lint test-floor package

CI_TARGETS := frontend-build \
	lint test-floor test-e2e \
	migration-check migration-check-postgres package docker \
	menubar-build

ci-targets:
	@printf '%s\n' $(CI_TARGETS)

ci:
	python3 scripts/local_ci.py run $(or $(REF),HEAD)

ci-doctor:
	python3 scripts/local_ci.py doctor

menubar-build:
	$(MAKE) -C clients/macos-menubar bundle
	codesign --verify --deep clients/macos-menubar/AgentLB.app
	plutil -lint clients/macos-menubar/AgentLB.app/Contents/Info.plist
