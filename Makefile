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
check: ## Run code quality tools.
	@echo "🚀 Checking lock file consistency with 'pyproject.toml'"
	@uv lock --locked
	@echo "🚀 Linting code: Running prek"
	@uv run prek run -a
	@echo "🚀 Static type checking: Running ty"
	@uv run ty check

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
	@uv sync --project e2e/bub

.PHONY: harness-check
harness-check: ## Validate the Bub replay harness and committed scenarios.
	@uv run ruff check e2e/bub
	@uv run ruff format --check e2e/bub
	@uv run ty check --project e2e/bub --python e2e/bub/.venv e2e/bub/src integrations/bub/src
	@uv run --project e2e/bub powercontext-e2e --help >/dev/null

.PHONY: harness-acceptance
harness-acceptance: ## Run all deterministic Bub session replay scenarios against a Server.
	@uv run --project e2e/bub powercontext-e2e acceptance e2e/bub/scenarios/*.yaml \
		--output "$${POWERCONTEXT_E2E_OUTPUT:-e2e/bub/results}"

.PHONY: harness-live
harness-live: ## Run one real-model Bub session replay scenario against a Server.
	@uv run --project e2e/bub powercontext-e2e live \
		"$${POWERCONTEXT_E2E_SCENARIO:-e2e/bub/scenarios/project-database-decision.yaml}" \
		--output "$${POWERCONTEXT_E2E_OUTPUT:-e2e/bub/results/live}"

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
harness-compose-acceptance: ## Build and run deterministic replay scenarios in containers.
	@e2e/bub/run.sh acceptance

.PHONY: harness-compose-live
harness-compose-live: ## Build and run one real-model replay in containers.
	@e2e/bub/run.sh live

.PHONY: harness-compose-down
harness-compose-down: ## Stop both isolated harness environments and remove their volumes.
	@POWERCONTEXT_E2E_DATABASE=sqlite e2e/bub/run.sh down
	@POWERCONTEXT_E2E_DATABASE=oceanbase e2e/bub/run.sh down

.PHONY: contract-test
contract-test: api-generate-check ## Verify generated API code and contract bindings.
	@uv run python -m pytest tests/test_api_contract.py

.PHONY: api-generate
api-generate: ## Generate API models and operations from OpenAPI.
	@uv run python scripts/generate_api.py

.PHONY: api-generate-check
api-generate-check: ## Verify generated API code is current.
	@uv run python scripts/generate_api.py --check

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

.PHONY: docs-test
docs-test: ## Test if documentation can be built without warnings or errors
	@uv run zensical build -s

.PHONY: docs
docs: ## Build and serve the documentation
	@uv run zensical serve

.PHONY: help
help:
	@uv run python -c "import re; \
	[[print(f'\033[36m{m[0]:<20}\033[0m {m[1]}') for m in re.findall(r'^([a-zA-Z0-9_-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.DEFAULT_GOAL := help
