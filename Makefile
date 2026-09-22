PYTEST_ARGS := -v -ra -o faulthandler_timeout=300 -o faulthandler_exit_on_timeout=true --timeout=180 --timeout-method=thread --durations=20
POSTGRES_TEST_DATABASE_URL ?=
CI_RUN_ID := $(shell date +%s)-$(shell echo $$$$)
CI_IMAGE ?= ghcr.io/aneym/agent-lb-ci:$(CI_RUN_ID)
CI_CLUSTER ?= agent-lb-ci-$(CI_RUN_ID)
CI_BASE_REF ?= origin/main
CI_HEAD_REF ?=
CI_EVENT_PATH ?=
PYTHON ?= .venv/bin/python
POSTGRES_PYTEST_TARGETS := \
	tests/integration/test_migrations.py::test_postgresql_migration_contract_policy_and_drift_match \
	tests/integration/test_migrations.py::test_postgresql_upgrade_head_from_empty_database \
	tests/integration/test_migrations.py::test_postgresql_startup_migration_auto_remap_legacy_head \
	tests/integration/test_usage_repository.py::test_latest_by_account_primary_query_plan_uses_normalized_window_index_postgresql \
	tests/integration/test_repositories.py::test_accounts_upsert_with_merge_enabled_serializes_concurrent_same_email \
	tests/integration/test_proxy_api_extended.py::test_proxy_stream_usage_limit_returns_http_error \
	tests/integration/test_repositories.py::test_accounts_upsert_with_merge_disabled_uses_identity_lock_on_postgresql
SHELL := /bin/bash

.PHONY: help
help:
	@printf '%s\n' \
	  'Common targets:' \
	  '  make lint                    ruff check + format check + architecture checks' \
	  '  make architecture-check      proxy architecture fitness ratchets' \
	  '  make typecheck               ty check' \
	  '  make frontend-test           vitest coverage, same as CI' \
	  '  make test-unit               unit pytest slice, same as CI' \
	  '  make test-integration-core   integration-core pytest slice' \
	  '  make package                 build and verify sdist/wheel' \
	  '  make startup-benchmark       record 5 isolated startup/ready samples' \
	  '  make ci-fast                 lint/type/frontend/unit/package' \
	  '  make ci                      full local CI gate'

.PHONY: frontend-install frontend-lint frontend-typecheck frontend-test frontend-build
frontend-install:
	cd frontend && bun install --frozen-lockfile

frontend-lint: frontend-install
	cd frontend && bun run lint

frontend-typecheck: frontend-install
	cd frontend && bun run typecheck

frontend-test: frontend-install
	cd frontend && bun run test:coverage

frontend-build: frontend-install
	cd frontend && bun run build

.PHONY: lint typecheck architecture-check
lint: architecture-check
	uv run ruff check .
	uv run ruff format --check .

architecture-check:
	$(PYTHON) scripts/check_proxy_architecture.py

typecheck:
	uv sync --dev --frozen
	uv run ty check

.PHONY: test-unit test-integration-core test-integration-bridge test-e2e test-postgres
test-unit: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/unit tests/test_request_logs_options_api.py

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

.PHONY: helm-deps helm-lint helm-template helm-kubeconform
helm-deps:
	helm dependency build deploy/helm/agent-lb/

helm-lint: helm-deps
	helm lint --strict deploy/helm/agent-lb/ --set postgresql.auth.password=test-password
	helm lint --strict deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-dev.yaml --set postgresql.auth.password=test-password
	helm lint --strict deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-bundled.yaml --set postgresql.auth.password=test-password
	helm lint --strict deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-external-db.yaml --set externalDatabase.url=postgresql+asyncpg://test:test@localhost/test
	helm lint --strict deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-external-secrets.yaml --set externalSecrets.secretStoreRef.name=test-store
	helm lint --strict deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-staging.yaml --set externalDatabase.url=postgresql+asyncpg://test:test@localhost/test
	helm lint --strict deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-prod.yaml --set externalSecrets.secretStoreRef.name=test-store

helm-template:
	helm template agent-lb deploy/helm/agent-lb/ --set postgresql.auth.password=test-password > /dev/null
	helm template agent-lb deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-dev.yaml --set postgresql.auth.password=test-password > /dev/null
	helm template agent-lb deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-bundled.yaml --set postgresql.auth.password=test-password > /dev/null
	helm template agent-lb deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-external-db.yaml --set externalDatabase.url=postgresql+asyncpg://test:test@localhost/test > /dev/null
	helm template agent-lb deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-external-secrets.yaml --set externalSecrets.secretStoreRef.name=test-store > /dev/null
	helm template agent-lb deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-staging.yaml --set externalDatabase.url=postgresql+asyncpg://test:test@localhost/test > /dev/null
	helm template agent-lb deploy/helm/agent-lb/ -f deploy/helm/agent-lb/values-prod.yaml --set externalSecrets.secretStoreRef.name=test-store > /dev/null

helm-kubeconform:
	set -e -o pipefail; \
	for version in 1.32.0 1.35.0; do \
	  helm template agent-lb deploy/helm/agent-lb/ \
	    -f deploy/helm/agent-lb/values-prod.yaml \
	    --set externalSecrets.secretStoreRef.name=test \
	    --set externalSecrets.secretStoreRef.kind=SecretStore \
	    --set gatewayApi.enabled=true \
	    --set "gatewayApi.parentRefs[0].name=test-gw" \
	    --set "gatewayApi.hostnames[0]=test.example.com" \
	    | kubeconform \
	      -strict \
	      -kubernetes-version "$${version}" \
	      -schema-location default \
	      -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
	      -summary; \
	done

.PHONY: helm-check helm-smoke-kind
helm-check: helm-lint helm-template helm-kubeconform

helm-smoke-kind:
	@set -e; \
	if kind get clusters | grep -Fxq "$(CI_CLUSTER)"; then echo "Refusing existing cluster $(CI_CLUSTER)"; exit 1; fi; \
	trap 'kind delete cluster --name "$(CI_CLUSTER)"' EXIT; \
	kind create cluster --name "$(CI_CLUSTER)" --image kindest/node:v1.35.0 --wait 120s; \
	docker build -t $(CI_IMAGE) .; \
	kind load docker-image $(CI_IMAGE) --name "$(CI_CLUSTER)"; \
	KUBE_CONTEXT=kind-$(CI_CLUSTER) IMAGE_REGISTRY=ghcr.io IMAGE_REPOSITORY=aneym/agent-lb-ci IMAGE_TAG=$$(echo $(CI_IMAGE) | cut -d: -f2) ./scripts/helm-kind-smoke.sh bundled; \
	KUBE_CONTEXT=kind-$(CI_CLUSTER) IMAGE_REGISTRY=ghcr.io IMAGE_REPOSITORY=aneym/agent-lb-ci IMAGE_TAG=$$(echo $(CI_IMAGE) | cut -d: -f2) ./scripts/helm-kind-smoke.sh external-db

.PHONY: ci-fast ci ci-targets ci-doctor menubar-test menubar-build contributors beta-release-guard
ci-fast: lint typecheck frontend-test test-unit package

CI_TARGETS := contributors beta-release-guard frontend-lint frontend-typecheck frontend-test frontend-build \
	lint typecheck test-unit test-integration-core test-integration-bridge test-e2e \
	test-postgres migration-check migration-check-postgres package docker helm-check helm-smoke-kind \
	menubar-test menubar-build

ci-targets:
	@printf '%s\n' $(CI_TARGETS)

ci:
	python3 scripts/local_ci.py run $(or $(REF),HEAD)

ci-doctor:
	python3 scripts/local_ci.py doctor

contributors:
	$(PYTHON) .github/scripts/check_all_contributors.py

beta-release-guard:
	$(PYTHON) -m scripts.guard_beta_release --mode pr --base-ref "$(CI_BASE_REF)" --head-ref "$(CI_HEAD_REF)" --event-path "$(CI_EVENT_PATH)"

menubar-test:
	cd clients/macos-menubar && swift test

menubar-build:
	$(MAKE) -C clients/macos-menubar bundle
	codesign --verify --deep clients/macos-menubar/AgentLB.app
	plutil -lint clients/macos-menubar/AgentLB.app/Contents/Info.plist
