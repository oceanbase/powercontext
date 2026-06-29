.PHONY: help install install-dev test test-unit test-integration test-e2e test-coverage test-fast test-slow test-claude-hook-docker check-python-version lint lint-full lint-pylint format clean build build-package build-check build-mcp-package build-mcp-check build-all-python-packages build-dashboard build-claude-hook package-claude-plugin publish-pypi publish-mcp-pypi publish-all-pypi publish-testpypi install-build-tools upload docs bump-version check-package-versions server-start server-stop server-restart server-status server-logs server-dashboard-start docker-build docker-run docker-up docker-down docker-logs docker-stop docker-restart docker-clean docker-ps

UV ?= uv
UV_PYTHON ?= 3.11
UV_RUN = $(UV) run --no-project --python $(UV_PYTHON)
UV_DEV = $(UV_RUN) --with-editable ".[dev]"
UV_TEST = $(UV_RUN) --with-editable ".[dev,test,server]"
UV_SERVER = $(UV_RUN) --with-editable ".[server,seekdb]"
UV_TWINE = $(UV_RUN) --with twine python -m twine
PYTHON = $(UV_RUN) python
VENV_PYTHON = .venv/bin/python

help: ## Show help information
	@echo "powermem Project Build Tools"
	@echo ""
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install project dependencies
	$(UV) venv --python $(UV_PYTHON)
	$(UV) pip install --python $(VENV_PYTHON) -e .

install-dev: ## Install development dependencies
	$(UV) venv --python $(UV_PYTHON)
	$(UV) pip install --python $(VENV_PYTHON) -e ".[dev]"

install-test: ## Install test dependencies
	$(UV) venv --python $(UV_PYTHON)
	$(UV) pip install --python $(VENV_PYTHON) -e ".[dev,test,server,seekdb]"

# Test commands
test: ## Run all tests (excludes all e2e tests)
	$(UV_TEST) pytest -m "not e2e and not e2e_config"

test-unit: ## Run unit tests only
	$(UV_TEST) pytest tests/unit/ -v

test-integration: ## Run integration tests only
	$(UV_TEST) pytest tests/integration/ -v

test-e2e: ## Run end-to-end tests only
	$(UV_TEST) pytest tests/e2e/ -v -m "e2e and not e2e_config"

test-e2e-config: ## Run end-to-end tests with real configuration (requires config files)
	$(UV_TEST) pytest tests/e2e/ -v -m e2e_config

test-fast: ## Run fast tests (exclude slow markers)
	$(UV_TEST) pytest -m "not slow" -v

test-slow: ## Run slow tests only
	$(UV_TEST) pytest -m "slow" -v

test-coverage: ## Run tests with coverage report
	$(UV_TEST) pytest --cov=src/powermem --cov-report=html --cov-report=term-missing --cov-report=xml -v

test-coverage-unit: ## Run unit tests with coverage
	$(UV_TEST) pytest tests/unit/ --cov=src/powermem --cov-report=html --cov-report=term-missing -v

test-coverage-integration: ## Run integration tests with coverage
	$(UV_TEST) pytest tests/integration/ --cov=src/powermem --cov-report=html --cov-report=term-missing -v

test-watch: ## Run tests in watch mode (requires pytest-watch)
	$(UV_TEST) --with pytest-watch ptw tests/ -- -v

test-verbose: ## Run tests with verbose output
	$(UV_TEST) pytest -vv

test-specific: ## Run specific test file (usage: make test-specific FILE=tests/unit/test_memory.py)
	$(UV_TEST) pytest $(FILE) -v

test-marker: ## Run tests with specific marker (usage: make test-marker MARKER=unit)
	$(UV_TEST) pytest -m $(MARKER) -v

CLAUDE_HOOK_REGRESSION_IMAGE ?= powermem-claude-hook-regression:local

test-claude-hook-docker: ## Run isolated no-LLM Claude Code hook regression tests in Docker
	docker build -t $(CLAUDE_HOOK_REGRESSION_IMAGE) -f docker/Dockerfile.claude-hook-regression .
	docker run --rm --network none \
		-e POWERMEM_TEST_BASE_URL=http://localhost:8848 \
		-e POWERMEM_BASE_URL=http://localhost:8848 \
		-e POWERMEM_INFER_TRANSCRIPT=0 \
		-e POWERMEM_INFER_COMPACT=0 \
		-e POWERMEM_INFER_FILE=0 \
		-e POWERMEM_PROMPT_SEARCH=1 \
		-e POWERMEM_HOOK_SCRUB=1 \
		-e POWERMEM_DATA_DIR=/tmp/powermem-data \
		$(CLAUDE_HOOK_REGRESSION_IMAGE)

# Code quality
check-python-version: ## Check Python version compatibility
	@$(PYTHON) -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "Python >= 3.11 is required; set PYTHON to a compatible interpreter.")'

lint: check-python-version ## Run high-signal linting checks
	$(UV_DEV) python -m flake8 src tests --select=F601,F821,E999

lint-full: check-python-version ## Run full flake8 report
	$(UV_DEV) python -m flake8 src tests

lint-pylint: check-python-version ## Run optional pylint checks
	$(UV_DEV) --with pylint python -m pylint src/powermem || true

format-check: ## Check code formatting
	$(UV_DEV) black --check src tests
	$(UV_DEV) isort --check-only src tests

format: ## Format code
	$(UV_DEV) black src tests
	$(UV_DEV) isort src tests

type-check: ## Run type checking with mypy
	$(UV_DEV) mypy src/powermem

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

check-package-versions: ## Verify powermem and powermem-mcp versions are aligned
	$(PYTHON) scripts/check_package_versions.py

build-package: clean check-package-versions ## Build distribution packages (wheel and sdist)
	@echo "Building distribution packages..."
	$(UV) build --python $(UV_PYTHON)
	@echo "Build complete! Distribution files are in dist/"
	@ls -lh dist/

build-check: build-package ## Check the built package
	@echo "Checking built package..."
	$(UV_TWINE) check dist/*
	@echo "Package check passed!"

build-mcp-package: check-package-versions ## Build powermem-mcp wrapper package
	@echo "Building powermem-mcp wrapper package..."
	rm -rf packages/powermem-mcp/dist/
	rm -rf packages/powermem-mcp/build/
	rm -rf packages/powermem-mcp/*.egg-info/
	rm -rf packages/powermem-mcp/src/*.egg-info/
	$(UV) build --python $(UV_PYTHON) --out-dir packages/powermem-mcp/dist packages/powermem-mcp
	@echo "Build complete! Distribution files are in packages/powermem-mcp/dist/"
	@ls -lh packages/powermem-mcp/dist/

build-mcp-check: build-mcp-package ## Check the powermem-mcp wrapper package
	@echo "Checking powermem-mcp wrapper package..."
	$(UV_TWINE) check packages/powermem-mcp/dist/*
	@echo "powermem-mcp package check passed!"

build-all-python-packages: build-check build-mcp-check ## Build and check powermem plus powermem-mcp

build-dashboard: ## Build dashboard frontend and inject into src/server/dashboard (for local dev; then use make server-start-reload)
	@echo "Building dashboard..."
	@if command -v pnpm >/dev/null 2>&1; then \
		cd dashboard && pnpm install && pnpm build; \
	else \
		echo "Using npm (install pnpm for faster installs: npm install -g pnpm)"; \
		cd dashboard && npm install && npm run build; \
	fi
	@echo "Injecting dashboard into src/server/dashboard..."
	@mkdir -p src/server/dashboard
	@cp -r dashboard/dist/* src/server/dashboard/
	@echo "✓ Dashboard built. Start server with: make server-start-reload (then open http://localhost:$(SERVER_PORT)/dashboard/)"

build-claude-hook: ## Build Claude Code hook binaries (Go; output: apps/claude-code-plugin/hooks/bin/)
	@bash apps/claude-code-plugin/scripts/build-hook-binaries.sh

package-claude-plugin: ## Zip Claude Code plugin for sharing (apps/claude-code-plugin/dist/*.zip)
	@bash apps/claude-code-plugin/scripts/package-plugin.sh

install-build-tools: ## Install build and upload tools
	@echo "Installing build tools..."
	$(UV) tool install twine
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
	$(UV_TWINE) upload dist/*
	@echo "Upload complete!"
	@echo "Package available at: https://pypi.org/project/powermem/"

publish-mcp-pypi: build-mcp-check ## Publish powermem-mcp to PyPI (requires credentials)
	@echo "Publishing powermem-mcp to PyPI..."
	@echo "Make sure the matching powermem version is already available on PyPI."
	@read -p "Continue with upload to PyPI? (y/N) " -n 1 -r; \
	echo; \
	if [[ ! $$REPLY =~ ^[Yy]$$ ]]; then \
		echo "Upload cancelled."; \
		exit 1; \
	fi
	$(UV_TWINE) upload packages/powermem-mcp/dist/*
	@echo "Upload complete!"
	@echo "Package available at: https://pypi.org/project/powermem-mcp/"

publish-all-pypi: publish-pypi publish-mcp-pypi ## Publish powermem first, then powermem-mcp

publish-testpypi: build-check ## Publish to TestPyPI (for testing)
	@echo "Publishing to TestPyPI..."
	@read -p "Continue with upload to TestPyPI? (y/N) " -n 1 -r; \
	echo; \
	if [[ ! $$REPLY =~ ^[Yy]$$ ]]; then \
		echo "Upload cancelled."; \
		exit 1; \
	fi
	$(UV_TWINE) upload --repository testpypi dist/*
	@echo "Upload complete!"
	@echo "Package available at: https://test.pypi.org/project/powermem/"

install-local: build-package ## Install package locally from dist/
	@echo "Installing package locally..."
	$(UV) venv --python $(UV_PYTHON)
	$(UV) pip install --python $(VENV_PYTHON) --force-reinstall dist/powermem-*.whl
	@echo "Package installed locally!"

# Benchmark and performance
benchmark: ## Run performance tests
	$(PYTHON) scripts/benchmark.py

# Setup
setup-env: ## Setup development environment
	$(PYTHON) scripts/setup.py

# Version management
# macOS BSD sed: -i requires a backup extension; use '' for in-place without backup.
# GNU sed accepts plain -i; '' is also valid. \+ in patterns is GNU-specific; use -E and + for portability.
SED_INPLACE := $(if $(filter Darwin,$(shell uname -s)),sed -i '',sed -i)

bump-version: ## Bump version number (usage: make bump-version VERSION=0.2.0)
	@if [ -z "$(VERSION)" ]; then \
		echo "Error: VERSION is required. Usage: make bump-version VERSION=0.2.0"; \
		exit 1; \
	fi
	@echo "Bumping version to $(VERSION)..."
	@# Update pyproject.toml
	@$(SED_INPLACE) 's/^version = ".*"/version = "$(VERSION)"/' pyproject.toml
	@# Update src/powermem/version.py
	@$(SED_INPLACE) 's/^__version__ = ".*"/__version__ = "$(VERSION)"/' src/powermem/version.py
	@# Update src/powermem/core/telemetry.py (all occurrences; match any x.y.z)
	@$(SED_INPLACE) -E 's/"version": "[0-9]+\.[0-9]+\.[0-9]+"/"version": "$(VERSION)"/g' src/powermem/core/telemetry.py
	@# Update src/powermem/core/audit.py (match any x.y.z)
	@$(SED_INPLACE) -E 's/"version": "[0-9]+\.[0-9]+\.[0-9]+"/"version": "$(VERSION)"/g' src/powermem/core/audit.py
	@# Update powermem-mcp wrapper package version and dependency pin
	@$(SED_INPLACE) 's/^version = ".*"/version = "$(VERSION)"/' packages/powermem-mcp/pyproject.toml
	@$(SED_INPLACE) -E 's/powermem\[server,seekdb\]==[0-9]+\.[0-9]+\.[0-9]+/powermem[server,seekdb]==$(VERSION)/' packages/powermem-mcp/pyproject.toml
	@$(MAKE) check-package-versions
	@echo "✓ Version updated to $(VERSION) in all files (excluding examples/)"
	@echo ""
	@echo "Updated files:"
	@echo "  - pyproject.toml"
	@echo "  - src/powermem/version.py"
	@echo "  - src/powermem/core/telemetry.py"
	@echo "  - src/powermem/core/audit.py"
	@echo "  - packages/powermem-mcp/pyproject.toml"
	@echo ""
	@echo "Note: Don't forget to update VERSION_HISTORY in src/powermem/version.py manually!"

# Server management
SERVER_PID_FILE := .server.pid

# Load server configuration from .env file if it exists
# This allows users to configure POWERMEM_SERVER_PORT, POWERMEM_SERVER_HOST, etc. in .env
# Read from .env file, stripping quotes and whitespace
ENV_SERVER_HOST := $(shell grep -E '^POWERMEM_SERVER_HOST=' .env 2>/dev/null | cut -d '=' -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$$//' | sed "s/^['\"]//;s/['\"]$$//")
ENV_SERVER_PORT := $(shell grep -E '^POWERMEM_SERVER_PORT=' .env 2>/dev/null | cut -d '=' -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$$//' | sed "s/^['\"]//;s/['\"]$$//")
ENV_SERVER_WORKERS := $(shell grep -E '^POWERMEM_SERVER_WORKERS=' .env 2>/dev/null | cut -d '=' -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$$//' | sed "s/^['\"]//;s/['\"]$$//")

# Use values from .env if they exist and are non-empty, otherwise use defaults
SERVER_HOST := $(or $(ENV_SERVER_HOST),0.0.0.0)
SERVER_PORT := $(or $(ENV_SERVER_PORT),8848)
SERVER_WORKERS := $(or $(ENV_SERVER_WORKERS),4)
SERVER_BROWSER_ARGS ?=
SERVER_PORT_LISTEN_PIDS = lsof -nP -t -iTCP:$(SERVER_PORT) -sTCP:LISTEN 2>/dev/null || true
SERVER_PORT_BIND_CHECK = $(PYTHON) -c 'import socket, sys; s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", int(sys.argv[1]))); s.close()' $(SERVER_PORT)

server-start: ## Start the PowerMem API server
	@echo "Starting PowerMem API server..."
	@if [ -f $(SERVER_PID_FILE) ]; then \
		echo "Server is already running (PID: $$(cat $(SERVER_PID_FILE)))"; \
		echo "Use 'make server-stop' to stop it first, or 'make server-restart' to restart"; \
		exit 1; \
	fi
	@PORT_PID=$$($(SERVER_PORT_LISTEN_PIDS)); \
	if [ -n "$$PORT_PID" ]; then \
		echo "Port $(SERVER_PORT) is already in use by PID(s): $$PORT_PID"; \
		exit 1; \
	fi; \
	if ! $(SERVER_PORT_BIND_CHECK) >/dev/null 2>&1; then \
		echo "Port $(SERVER_PORT) is not available for binding"; \
		exit 1; \
	fi; \
		$(UV_SERVER) powermem-server --host $(SERVER_HOST) --port $(SERVER_PORT) --workers $(SERVER_WORKERS) $(SERVER_BROWSER_ARGS) >> server.log 2>&1 & \
	PID=$$!; \
	echo $$PID > $(SERVER_PID_FILE); \
	sleep 1; \
	if ! kill -0 $$PID 2>/dev/null; then \
		echo "Server process exited during startup. Last 80 lines of server.log:"; \
		tail -n 80 server.log 2>/dev/null || true; \
		rm -f $(SERVER_PID_FILE); \
		exit 1; \
	fi; \
	echo "Server started with PID: $$PID"; \
	echo "Server running at http://$(SERVER_HOST):$(SERVER_PORT)"; \
	echo "API docs available at http://$(SERVER_HOST):$(SERVER_PORT)/docs"; \
	echo "Process output is appended to server.log"; \
	echo "Structured logs are configured via POWERMEM_SERVER_LOG_FILE"; \
	echo "Use 'make server-stop' to stop the server"

server-start-reload: ## Start the PowerMem API server with auto-reload (development mode)
	@echo "Starting PowerMem API server with auto-reload..."
	@if [ -f $(SERVER_PID_FILE) ]; then \
		echo "Server is already running (PID: $$(cat $(SERVER_PID_FILE)))"; \
		echo "Use 'make server-stop' to stop it first"; \
		exit 1; \
	fi
	@PORT_PID=$$($(SERVER_PORT_LISTEN_PIDS)); \
	if [ -n "$$PORT_PID" ]; then \
		echo "Port $(SERVER_PORT) is already in use by PID(s): $$PORT_PID"; \
		exit 1; \
	fi; \
	if ! $(SERVER_PORT_BIND_CHECK) >/dev/null 2>&1; then \
		echo "Port $(SERVER_PORT) is not available for binding"; \
		exit 1; \
	fi; \
		$(UV_SERVER) powermem-server --host $(SERVER_HOST) --port $(SERVER_PORT) --reload $(SERVER_BROWSER_ARGS) >> server.log 2>&1 & \
	PID=$$!; \
	echo $$PID > $(SERVER_PID_FILE); \
	sleep 1; \
	if ! kill -0 $$PID 2>/dev/null; then \
		echo "Server process exited during startup. Last 80 lines of server.log:"; \
		tail -n 80 server.log 2>/dev/null || true; \
		rm -f $(SERVER_PID_FILE); \
		exit 1; \
	fi; \
	echo "Server started with PID: $$PID (auto-reload enabled)"; \
	echo "Server running at http://$(SERVER_HOST):$(SERVER_PORT)"; \
	echo "API docs available at http://$(SERVER_HOST):$(SERVER_PORT)/docs"; \
	echo "Process output is appended to server.log"; \
	echo "Structured logs are configured via POWERMEM_SERVER_LOG_FILE"; \
	echo "Use 'make server-stop' to stop the server"

server-stop: ## Stop the PowerMem API server
	@PID=$$(cat $(SERVER_PID_FILE) 2>/dev/null || true); \
	if [ -n "$$PID" ] && ps -p $$PID > /dev/null 2>&1; then \
		echo "Stopping server (PID: $$PID)..."; \
		kill $$PID 2>/dev/null || true; \
	else \
		if [ -f $(SERVER_PID_FILE) ]; then \
			echo "Server process not found from PID file, checking port $(SERVER_PORT)..."; \
		else \
			echo "Server PID file not found. Checking port $(SERVER_PORT)..."; \
		fi; \
	fi; \
	rm -f $(SERVER_PID_FILE); \
	for attempt in 1 2 3 4 5 6 7 8 9 10; do \
		PID_ALIVE=0; \
		if [ -n "$$PID" ] && ps -p $$PID > /dev/null 2>&1; then \
			PID_ALIVE=1; \
		fi; \
		PORT_PID=$$($(SERVER_PORT_LISTEN_PIDS)); \
		if [ -z "$$PORT_PID" ] && $(SERVER_PORT_BIND_CHECK) >/dev/null 2>&1; then \
			echo "✓ Server stopped"; \
			exit 0; \
		fi; \
		if [ "$$attempt" = "1" ] && [ -n "$$PORT_PID" ]; then \
			echo "Found process(es) on port $(SERVER_PORT), stopping: $$PORT_PID"; \
			kill $$PORT_PID 2>/dev/null || true; \
		fi; \
		if [ "$$attempt" = "10" ]; then \
			if [ "$$PID_ALIVE" = "1" ]; then \
				echo "Force killing server (PID: $$PID)..."; \
				kill -9 $$PID 2>/dev/null || true; \
			fi; \
			if [ -n "$$PORT_PID" ]; then \
				echo "Force killing process(es) on port $(SERVER_PORT): $$PORT_PID"; \
				kill -9 $$PORT_PID 2>/dev/null || true; \
			fi; \
		fi; \
		sleep 1; \
	done; \
	PORT_PID=$$($(SERVER_PORT_LISTEN_PIDS)); \
	if [ -n "$$PORT_PID" ]; then \
		echo "Force killing process(es) on port $(SERVER_PORT): $$PORT_PID"; \
		kill -9 $$PORT_PID 2>/dev/null || true; \
	fi; \
	for attempt in 1 2 3 4 5; do \
		PORT_PID=$$($(SERVER_PORT_LISTEN_PIDS)); \
		if [ -z "$$PORT_PID" ] && $(SERVER_PORT_BIND_CHECK) >/dev/null 2>&1; then \
			echo "✓ Server stopped"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Error: port $(SERVER_PORT) is still occupied after server-stop"; \
	exit 1

server-restart: server-stop server-start ## Restart the PowerMem API server
	@echo "✓ Server restarted"

server-status: ## Check the status of the PowerMem API server
	@if [ -f $(SERVER_PID_FILE) ]; then \
		PID=$$(cat $(SERVER_PID_FILE) 2>/dev/null || echo ""); \
		if [ -z "$$PID" ]; then \
			echo "Server PID file exists but is empty"; \
			rm -f $(SERVER_PID_FILE); \
			exit 1; \
		fi; \
		if ps -p $$PID > /dev/null 2>&1; then \
			echo "✓ Server is running (PID: $$PID)"; \
			echo "  URL: http://$(SERVER_HOST):$(SERVER_PORT)"; \
			echo "  Docs: http://$(SERVER_HOST):$(SERVER_PORT)/docs"; \
			echo "  Health: http://$(SERVER_HOST):$(SERVER_PORT)/api/v1/health"; \
		else \
			echo "✗ Server is not running (stale PID file)"; \
			rm -f $(SERVER_PID_FILE); \
			exit 1; \
		fi; \
	else \
		PID=$$(lsof -t -i:$(SERVER_PORT) 2>/dev/null || echo ""); \
		if [ -z "$$PID" ]; then \
			echo "✗ Server is not running"; \
			exit 1; \
		else \
			echo "✓ Server is running on port $(SERVER_PORT) (PID: $$PID)"; \
			echo "  URL: http://$(SERVER_HOST):$(SERVER_PORT)"; \
			echo "  Docs: http://$(SERVER_HOST):$(SERVER_PORT)/docs"; \
		fi; \
	fi

server-logs: ## Show server logs (tail -f server.log)
	@if [ ! -f server.log ]; then \
		echo "No log file found (server.log)"; \
		exit 1; \
	fi
	@tail -f server.log

server-logs-last: ## Show last 50 lines of server logs
	@if [ ! -f server.log ]; then \
		echo "No log file found (server.log)"; \
		exit 1; \
	fi
	@tail -n 50 server.log

server-dashboard-start: ## Build dashboard, then start server (stops existing server first)
	@echo "[1/3] Stopping service (if running)..."
	@$(MAKE) -s server-stop 2>/dev/null || true
	@echo "[2/3] Building dashboard..."
	@$(MAKE) build-dashboard
	@echo "[3/3] Starting server..."
	@$(MAKE) server-start SERVER_BROWSER_ARGS=--open-browser
	@echo "✓ Dashboard at http://$(SERVER_HOST):$(SERVER_PORT)/dashboard/"

# Docker commands
DOCKER_IMAGE := oceanbase/powermem-server
DOCKER_TAG := latest
DOCKER_COMPOSE_FILE := docker/docker-compose.yml

docker-build: ## Build Docker image
	@echo "Building Docker image $(DOCKER_IMAGE):$(DOCKER_TAG)..."
	docker build -t $(DOCKER_IMAGE):$(DOCKER_TAG) -f docker/Dockerfile .
	@echo "✓ Docker image built successfully"

docker-build-mirror: ## Build Docker image with Python package mirror source (usage: make docker-build-mirror MIRROR=tsinghua)
	@if [ -z "$(MIRROR)" ]; then \
		echo "Error: MIRROR is required. Usage: make docker-build-mirror MIRROR=tsinghua"; \
		echo "Available mirrors: tsinghua, aliyun"; \
		exit 1; \
	fi
	@case "$(MIRROR)" in \
		tsinghua) \
			PIP_URL="https://pypi.tuna.tsinghua.edu.cn/simple"; \
			PIP_HOST="pypi.tuna.tsinghua.edu.cn"; \
			DEBIAN_MIRROR="mirrors.tuna.tsinghua.edu.cn"; \
			;; \
		aliyun) \
			PIP_URL="https://mirrors.aliyun.com/pypi/simple"; \
			PIP_HOST="mirrors.aliyun.com"; \
			DEBIAN_MIRROR="mirrors.aliyun.com"; \
			;; \
		*) \
			echo "Error: Unknown mirror '$(MIRROR)'. Available: tsinghua, aliyun"; \
			exit 1; \
			;; \
	esac; \
	echo "Building Docker image $(DOCKER_IMAGE):$(DOCKER_TAG) with $(MIRROR) mirror..."; \
	docker build -t $(DOCKER_IMAGE):$(DOCKER_TAG) -f docker/Dockerfile \
		--build-arg PIP_INDEX_URL=$$PIP_URL \
		--build-arg PIP_TRUSTED_HOST=$$PIP_HOST \
		--build-arg DEBIAN_MIRROR=$$DEBIAN_MIRROR .
	@echo "✓ Docker image built successfully with $(MIRROR) mirror"

docker-build-tag: ## Build Docker image with custom tag (usage: make docker-build-tag TAG=v0.2.1)
	@if [ -z "$(TAG)" ]; then \
		echo "Error: TAG is required. Usage: make docker-build-tag TAG=v0.2.1"; \
		exit 1; \
	fi
	@echo "Building Docker image $(DOCKER_IMAGE):$(TAG)..."
	docker build -t $(DOCKER_IMAGE):$(TAG) -f docker/Dockerfile .
	@echo "✓ Docker image built successfully with tag $(TAG)"

docker-run: ## Run Docker container
	@echo "Running Docker container..."
	@if [ ! -f .env ]; then \
		echo "Warning: .env file not found. Container will use default configuration."; \
	fi
	docker run -d \
		--name powermem-server \
		-p 8848:8848 \
		-v $$(pwd)/.env:/app/.env:ro \
		--env-file .env \
		$(DOCKER_IMAGE):$(DOCKER_TAG) || \
		(echo "Container may already exist. Use 'make docker-stop' first or 'make docker-restart'"; exit 1)
	@echo "✓ Container started"
	@echo "Server running at http://localhost:8848"
	@echo "API docs at http://localhost:8848/docs"

docker-up: ## Start services using docker-compose
	@echo "Starting services with docker-compose..."
	docker-compose -f $(DOCKER_COMPOSE_FILE) up -d
	@echo "✓ Services started"
	@echo "Server running at http://localhost:8848"
	@echo "API docs at http://localhost:8848/docs"

docker-down: ## Stop services using docker-compose
	@echo "Stopping services with docker-compose..."
	docker-compose -f $(DOCKER_COMPOSE_FILE) down
	@echo "✓ Services stopped"

docker-logs: ## Show Docker container logs (docker-compose)
	@docker-compose -f $(DOCKER_COMPOSE_FILE) logs -f

docker-logs-container: ## Show Docker container logs (single container)
	@docker logs -f powermem-server 2>/dev/null || echo "Container 'powermem-server' not found. Use 'make docker-run' first."

docker-stop: ## Stop Docker container
	@echo "Stopping Docker container..."
	@docker stop powermem-server 2>/dev/null && docker rm powermem-server 2>/dev/null && echo "✓ Container stopped and removed" || echo "Container not found or already stopped"

docker-restart: docker-stop docker-run ## Restart Docker container
	@echo "✓ Container restarted"

docker-restart-compose: docker-down docker-up ## Restart services using docker-compose
	@echo "✓ Services restarted"

docker-ps: ## Show running Docker containers
	@echo "Running containers:"
	@docker ps --filter "name=powermem-server" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

docker-status: ## Check Docker container status
	@if docker ps --filter "name=powermem-server" --format "{{.Names}}" | grep -q powermem-server; then \
		echo "✓ Container is running"; \
		docker ps --filter "name=powermem-server" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; \
	else \
		echo "✗ Container is not running"; \
		exit 1; \
	fi

docker-clean: ## Clean Docker resources (containers, images, volumes)
	@echo "Cleaning Docker resources..."
	@docker stop powermem-server 2>/dev/null || true
	@docker rm powermem-server 2>/dev/null || true
	@docker-compose -f $(DOCKER_COMPOSE_FILE) down -v 2>/dev/null || true
	@echo "✓ Docker resources cleaned"

docker-clean-all: ## Clean all Docker resources including images
	@echo "Cleaning all Docker resources (including images)..."
	@docker stop powermem-server 2>/dev/null || true
	@docker rm powermem-server 2>/dev/null || true
	@docker-compose -f $(DOCKER_COMPOSE_FILE) down -v 2>/dev/null || true
	@docker rmi $(DOCKER_IMAGE):$(DOCKER_TAG) 2>/dev/null || true
	@echo "✓ All Docker resources cleaned"

docker-rebuild: docker-clean docker-build ## Rebuild Docker image from scratch
	@echo "✓ Docker image rebuilt"

docker-rebuild-up: docker-rebuild docker-up ## Rebuild and start services
	@echo "✓ Docker image rebuilt and services started"

# CI/CD helpers
ci-test: install-test test-unit test-integration ## Run tests for CI (unit + integration)
	@echo "✓ All CI tests passed"

ci-full: install-test lint format-check test-coverage ## Run full CI checks
	@echo "✓ All CI checks passed"
