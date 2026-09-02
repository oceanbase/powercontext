.PHONY: install
install: ## Install the virtual environment and install the prek hooks
	@echo "🚀 Creating virtual environment using uv"
	@uv sync
	@uv run prek install

.PHONY: skills-install
skills-install: ## Install recommended agent skills from skills-lock.json
	@echo "🚀 Installing recommended agent skills"
	@npx skills experimental_install
	@echo "Restart Codex to pick up new skills."

.PHONY: check
check: integration-manifest-check ## Run code quality tools.
	@echo "🚀 Checking lock file consistency with 'pyproject.toml'"
	@uv lock --locked
	@echo "🚀 Linting code: Running prek"
	@uv run prek run -a
	@echo "🚀 Static type checking: Running ty"
	@uv run ty check
	@echo "🚀 Static type checking: Running ty for the Pydantic AI integration"
	@uv run ty check integrations/pydantic-ai/src

.PHONY: test
test: ## Test the code with pytest
	@echo "🚀 Testing code: Running pytest"
	@uv run python -m pytest --doctest-modules

.PHONY: unit-test
unit-test: ## Run tests that do not cross the Server boundary end to end.
	@uv run python -m pytest --doctest-modules --ignore=tests/e2e

.PHONY: e2e-test
e2e-test: ## Run CLI to Client SDK to Server end-to-end tests.
	@uv run python -m pytest tests/e2e

.PHONY: real-e2e-test
real-e2e-test: ## Run opt-in real Codex Experience/Skill tests; REAL_E2E_MODE defaults to all.
	@uv run python -m pytest -s tests/e2e/real_experience_skill --run-real-e2e \
		--real-e2e-mode="$${REAL_E2E_MODE:-all}" \
		--real-codex-timeout="$${REAL_CODEX_TIMEOUT:-600}" \
		--real-e2e-env-file="$${REAL_E2E_ENV_FILE:-.env}"

.PHONY: harness-sync
harness-sync: ## Install the Bub replay harness environment.
	@uv sync --project e2e/bub --locked

.PHONY: harness-check
harness-check: ## Validate the Bub replay harness and committed scenarios.
	@uv run ruff check e2e/bub
	@uv run ruff format --check e2e/bub
	@uv run ty check --project e2e/bub --python e2e/bub/.venv --python-version 3.12 e2e/bub/src integrations/bub/src
	@uv run --project e2e/bub python -m pytest e2e/bub/tests
	@uv run --project e2e/bub powercontext-e2e --help >/dev/null

.PHONY: harness-acceptance
harness-acceptance: ## Evaluate workloads by ID or category against an existing Server.
	@uv run --project e2e/bub powercontext-e2e acceptance \
		--output "$${POWERCONTEXT_E2E_OUTPUT:-e2e/bub/results}" $(ARGS)

.PHONY: harness-rescore
harness-rescore: ## Rescore REPLAY without rerunning Bub or PowerContext.
	@test -n "$${REPLAY:-}" || { echo "REPLAY is required" >&2; exit 2; }
	@uv run --project e2e/bub powercontext-e2e rescore "$${REPLAY}" \
		--output "$${POWERCONTEXT_E2E_OUTPUT:-e2e/bub/results/rescore}"

.PHONY: harness-compose-check
harness-compose-check: ## Validate the SQLite and OceanBase Compose environments.
	@POWERCONTEXT_E2E_DATABASE=sqlite e2e/bub/run.sh check
	@POWERCONTEXT_E2E_DATABASE=oceanbase e2e/bub/run.sh check

.PHONY: harness-compose-acceptance
harness-compose-acceptance: ## Build and evaluate workloads by ID or category in the fixed harness.
	@e2e/bub/run.sh acceptance $(ARGS)

.PHONY: harness-compose-down
harness-compose-down: ## Stop the selected isolated harness environment and remove its volumes.
	@e2e/bub/run.sh down

.PHONY: contract-test
contract-test: api-generate-check js-api-generate-check ## Verify generated API code and contract bindings.
	@uv run python -m pytest tests/test_api_contract.py tests/test_js_operations.py

.PHONY: api-generate
api-generate: ## Generate API models and operations from OpenAPI.
	@uv run python scripts/generate_api.py

.PHONY: api-generate-check
api-generate-check: ## Verify generated API code is current.
	@uv run python scripts/generate_api.py --check

.PHONY: js-api-generate
js-api-generate: ## Generate JavaScript integration operation tables from OpenAPI.
	@uv run python scripts/generate_js_operations.py

.PHONY: js-api-generate-check
js-api-generate-check: ## Verify generated JS operations are current.
	@uv run python scripts/generate_js_operations.py --check

.PHONY: js-test
js-test: ## Install, build, and test the DeepSeek Harness plugin.
	@pnpm --dir integrations/dsh/plugins/powercontext install --frozen-lockfile --config.auto-install-peers=false
	@pnpm --dir integrations/dsh/plugins/powercontext test
	@pnpm --dir integrations/dsh/plugins/powercontext build
	@git diff --exit-code -- \
		integrations/dsh/plugins/powercontext/openapi/powercontext.yaml \
		integrations/dsh/plugins/powercontext/src/operations.generated.ts \
		integrations/dsh/plugins/powercontext/lib
	@pnpm --dir integrations/dsh/plugins/powercontext test
	@pnpm --dir integrations/dsh/plugins/powercontext test:e2e

.PHONY: openclaw-plugin-build
openclaw-plugin-build: ## Build the external OpenClaw memory plugin.
	@pnpm --dir integrations/openclaw/plugins/memory-powercontext build

.PHONY: openclaw-plugin-pack
openclaw-plugin-pack: ## Build and pack the external OpenClaw memory plugin.
	@pnpm --dir integrations/openclaw/plugins/memory-powercontext pack:local

.PHONY: opencode-test
opencode-test: ## Install, test, type-check, and build the OpenCode plugin.
	@pnpm --dir integrations/opencode/plugins/powercontext install --frozen-lockfile
	@pnpm --dir integrations/opencode/plugins/powercontext test
	@pnpm --dir integrations/opencode/plugins/powercontext run typecheck
	@pnpm --dir integrations/opencode/plugins/powercontext run build

.PHONY: pi-test
pi-test: ## Install and test the Pi package.
	@pnpm --dir integrations/pi/plugins/powercontext install --frozen-lockfile
	@pnpm --dir integrations/pi/plugins/powercontext test
	@pnpm --dir integrations/pi/plugins/powercontext run typecheck

.PHONY: build
build: clean-build ## Build wheel file
	@echo "🚀 Creating wheel file"
	@uv build

.PHONY: clean-build
clean-build: ## Clean build artifacts
	@echo "🚀 Removing build artifacts"
	@uv run python -c "import shutil; import os; shutil.rmtree('dist') if os.path.exists('dist') else None"

.PHONY: publish
publish: ## Publish a release to PyPI.
	@echo "🚀 Publishing."
	@uv publish dist/*

.PHONY: build-and-publish
build-and-publish: build publish ## Build and publish.

.PHONY: docs-build
docs-build: ## Build the documentation and publish the canonical OpenAPI contract.
	@mkdir -p docs/api
	@install -m 0644 openapi/powercontext.yaml docs/api/openapi.yaml
	@trap 'rm -f docs/api/openapi.yaml' EXIT; uv run zensical build --clean -s

.PHONY: docs-test
docs-test: docs-build ## Test if documentation can be built without warnings or errors
	@test -f site/api/index.html
	@cmp --silent openapi/powercontext.yaml site/api/openapi.yaml

.PHONY: integration-manifest-docs
integration-manifest-docs: ## Generate the checked-in integration capability matrix pages.
	@uv run python scripts/generate_integration_manifest_docs.py

.PHONY: integration-manifest-docs-check
integration-manifest-docs-check: ## Verify the integration capability matrix pages are current.
	@uv run python scripts/generate_integration_manifest_docs.py --check

.PHONY: integration-manifest-check
integration-manifest-check: integration-manifest-docs-check ## Verify the complete integration capability contract.
	@uv run python -m pytest tests/test_integration_manifest.py

.PHONY: docs
docs: ## Build and serve the documentation
	@mkdir -p docs/api
	@install -m 0644 openapi/powercontext.yaml docs/api/openapi.yaml
	@trap 'rm -f docs/api/openapi.yaml' EXIT; uv run zensical serve $(ARGS)

.PHONY: help
help:
	@uv run python -c "import re; \
	[[print(f'\033[36m{m[0]:<20}\033[0m {m[1]}') for m in re.findall(r'^([a-zA-Z0-9_-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.DEFAULT_GOAL := help
