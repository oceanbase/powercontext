.PHONY: install
install: ## Install the virtual environment and install the prek hooks
	@echo "🚀 Creating virtual environment using uv"
	@uv sync
	@uv run prek install

.PHONY: vec1-install
vec1-install: ## Build and install the pinned SQLite Vec1 extension locally
	@./scripts/install_vec1.sh

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
	@uvx --from build pyproject-build --installer uv

.PHONY: clean-build
clean-build: ## Clean build artifacts
	@echo "🚀 Removing build artifacts"
	@uv run python -c "import shutil; import os; shutil.rmtree('dist') if os.path.exists('dist') else None"

.PHONY: publish
publish: ## Publish a release to PyPI.
	@echo "🚀 Publishing."
	@uvx twine upload --repository-url https://upload.pypi.org/legacy/ dist/*

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
	[[print(f'\033[36m{m[0]:<20}\033[0m {m[1]}') for m in re.findall(r'^([a-zA-Z_-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.DEFAULT_GOAL := help
