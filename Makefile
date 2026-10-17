PYTEST_ARGS := -v -ra -o faulthandler_timeout=300 -o faulthandler_exit_on_timeout=true --timeout=180 --timeout-method=thread --durations=20
POSTGRES_TEST_DATABASE_URL ?=
CI_RUN_ID := $(shell date +%s)-$(shell echo $$$$)
CI_IMAGE ?= ghcr.io/aneym/agent-lb-ci:$(CI_RUN_ID)
CI_BASE_REF ?= origin/main
CI_HEAD_REF ?=
CI_EVENT_PATH ?=
PYTHON ?= .venv/bin/python
POSTGRES_PYTEST_TARGETS := \
	tests/integration/test_migrations.py::test_postgresql_migration_contract_policy_and_drift_match \
	tests/integration/test_migrations.py::test_postgresql_upgrade_head_from_empty_database \
	tests/integration/test_usage_repository.py::test_latest_by_account_primary_query_plan_uses_normalized_window_index_postgresql \
	tests/integration/test_repositories.py::test_accounts_upsert_with_merge_enabled_serializes_concurrent_same_email \
	tests/integration/test_proxy_api_extended.py::test_proxy_stream_usage_limit_returns_http_error \
	tests/integration/test_repositories.py::test_accounts_upsert_with_merge_disabled_uses_identity_lock_on_postgresql \
	tests/integration/test_refresh_cross_process.py \
	tests/integration/test_exchange_uncertain_selection.py \
	tests/integration/test_migration_lock_timeout.py \
	tests/integration/test_federation_custody_races.py \
	tests/integration/test_federation_api.py::test_postgres_checkout_crash_before_and_after_commit_retries_without_double_ownership \
	tests/integration/test_federation_api.py::test_postgres_checkin_crash_before_commit_preserves_owner_and_old_token \
	tests/integration/test_federation_api.py::test_postgres_checkin_has_no_committed_import_before_owner_change \
	tests/integration/test_federation_api.py::test_mirror_snapshot_cannot_overwrite_concurrent_local_checkout
SHELL := /bin/bash

.PHONY: help
help:
	@printf '%s\n' \
	  'Common targets:' \
	  '  make lint                    factory-lint lint, format and type checks' \
	  '  make architecture-check      proxy architecture fitness ratchets' \
	  '  make test-floor              kept-floor pytest guards, same as CI' \
	  '  make test-integration-core   integration-core pytest slice' \
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

.PHONY: test-floor test-integration-core test-integration-bridge test-e2e test-postgres
test-floor:
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/floor

test-integration-core: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/integration \
	  --ignore=tests/integration/test_http_responses_bridge.py \
	  --ignore=tests/integration/test_proxy_websocket_responses.py

test-integration-bridge: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) -vv \
	  tests/integration/test_http_responses_bridge.py \
	  tests/integration/test_proxy_websocket_responses.py

test-e2e: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/e2e

test-postgres:
	@test -n "$(POSTGRES_TEST_DATABASE_URL)" || (echo "Set a disposable POSTGRES_TEST_DATABASE_URL"; exit 1)
	uv sync --dev --frozen
	AGENT_LB_TEST_DATABASE_URL="$(POSTGRES_TEST_DATABASE_URL)" \
	  PYTHONFAULTHANDLER=1 \
	  uv run pytest $(PYTEST_ARGS) $(POSTGRES_PYTEST_TARGETS)

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
	lint test-floor test-integration-core test-integration-bridge test-e2e \
	test-postgres migration-check migration-check-postgres package docker \
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
