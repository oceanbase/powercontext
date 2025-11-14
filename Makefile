.PHONY: help install install-dev test test-unit test-integration test-e2e test-coverage test-fast test-slow lint format clean build build-package build-check publish-pypi publish-testpypi install-build-tools upload docs

help: ## Show help information
	@echo "powermem Project Build Tools"
	@echo ""
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install project dependencies
	pip install -e .

install-dev: ## Install development dependencies
	pip install -e ".[dev]"

install-test: ## Install test dependencies
	pip install -e ".[dev,test,llm,vector_stores]"

# Test commands
test: ## Run all tests (excludes all e2e tests)
	pytest -m "not e2e and not e2e_config"

test-unit: ## Run unit tests only
	pytest tests/unit/ -v

test-integration: ## Run integration tests only
	pytest tests/integration/ -v

test-e2e: ## Run end-to-end tests only
	pytest tests/e2e/ -v -m "e2e and not e2e_config"

test-e2e-config: ## Run end-to-end tests with real configuration (requires config files)
	pytest tests/e2e/ -v -m e2e_config

test-fast: ## Run fast tests (exclude slow markers)
	pytest -m "not slow" -v

test-slow: ## Run slow tests only
	pytest -m "slow" -v

test-coverage: ## Run tests with coverage report
	pytest --cov=src/powermem --cov-report=html --cov-report=term-missing --cov-report=xml -v

test-coverage-unit: ## Run unit tests with coverage
	pytest tests/unit/ --cov=src/powermem --cov-report=html --cov-report=term-missing -v

test-coverage-integration: ## Run integration tests with coverage
	pytest tests/integration/ --cov=src/powermem --cov-report=html --cov-report=term-missing -v

test-watch: ## Run tests in watch mode (requires pytest-watch)
	ptw tests/ -- -v

test-verbose: ## Run tests with verbose output
	pytest -vv

test-specific: ## Run specific test file (usage: make test-specific FILE=tests/unit/test_memory.py)
	pytest $(FILE) -v

test-marker: ## Run tests with specific marker (usage: make test-marker MARKER=unit)
	pytest -m $(MARKER) -v

# Code quality
lint: ## Run linting checks
	flake8 src tests
	pylint src/powermem || true

format-check: ## Check code formatting
	black --check src tests
	isort --check-only src tests

format: ## Format code
	black src tests
	isort src tests

type-check: ## Run type checking with mypy
	mypy src/powermem

# Cleanup
clean: ## Clean build files
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .mypy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete

clean-test: ## Clean test artifacts only
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .mypy_cache/

# Build and Package
build: ## Build package (legacy, use build-package)
	@echo "Use 'make build-package' instead"
	$(MAKE) build-package

build-package: clean ## Build distribution packages (wheel and sdist)
	@echo "Building distribution packages..."
	python -m build
	@echo "Build complete! Distribution files are in dist/"
	@ls -lh dist/

build-check: build-package ## Check the built package
	@echo "Checking built package..."
	python -m twine check dist/*
	@echo "Package check passed!"

install-build-tools: ## Install build and upload tools
	@echo "Installing build tools..."
	pip install --upgrade build twine
	@echo "Build tools installed!"

# PyPI Publishing
publish-pypi: build-check ## Publish to PyPI (requires credentials)
	@echo "Publishing to PyPI..."
	@echo "Make sure you have:"
	@echo "   1. Updated version in pyproject.toml"
	@echo "   2. Tested the package locally"
	@echo "   3. Created a git tag for the version"
	@read -p "Continue with upload to PyPI? (y/N) " -n 1 -r; \
	echo; \
	if [[ ! $$REPLY =~ ^[Yy]$$ ]]; then \
		echo "Upload cancelled."; \
		exit 1; \
	fi
	python -m twine upload dist/*
	@echo "Upload complete!"
	@echo "Package available at: https://pypi.org/project/powermem/"

publish-testpypi: build-check ## Publish to TestPyPI (for testing)
	@echo "Publishing to TestPyPI..."
	@read -p "Continue with upload to TestPyPI? (y/N) " -n 1 -r; \
	echo; \
	if [[ ! $$REPLY =~ ^[Yy]$$ ]]; then \
		echo "Upload cancelled."; \
		exit 1; \
	fi
	python -m twine upload --repository testpypi dist/*
	@echo "Upload complete!"
	@echo "Package available at: https://test.pypi.org/project/powermem/"

install-local: build-package ## Install package locally from dist/
	@echo "Installing package locally..."
	pip install --force-reinstall dist/powermem-*.whl
	@echo "Package installed locally!"

# Benchmark and performance
benchmark: ## Run performance tests
	python scripts/benchmark.py

# Setup
setup-env: ## Setup development environment
	python scripts/setup.py

# CI/CD helpers
ci-test: install-test test-unit test-integration ## Run tests for CI (unit + integration)
	@echo "✓ All CI tests passed"

ci-full: install-test lint format-check test-coverage ## Run full CI checks
	@echo "✓ All CI checks passed"
