.PHONY: help install install-dev test test-unit test-integration test-e2e test-coverage test-fast test-slow lint format clean build build-package build-check publish-pypi publish-testpypi install-build-tools upload docs bump-version server-start server-stop server-restart server-status server-logs docker-build docker-run docker-up docker-down docker-logs docker-stop docker-restart docker-clean docker-ps

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

# Version management
bump-version: ## Bump version number (usage: make bump-version VERSION=0.2.0)
	@if [ -z "$(VERSION)" ]; then \
		echo "Error: VERSION is required. Usage: make bump-version VERSION=0.2.0"; \
		exit 1; \
	fi
	@echo "Bumping version to $(VERSION)..."
	@# Update pyproject.toml
	@sed -i 's/^version = ".*"/version = "$(VERSION)"/' pyproject.toml
	@# Update src/powermem/version.py
	@sed -i 's/^__version__ = ".*"/__version__ = "$(VERSION)"/' src/powermem/version.py
	@# Update examples/langgraph/__init__.py
	@sed -i 's/^__version__ = ".*"/__version__ = "$(VERSION)"/' examples/langgraph/__init__.py
	@# Update src/powermem/core/telemetry.py (all occurrences)
	@sed -i 's/"version": "0\.[0-9]\+\.[0-9]\+"/"version": "$(VERSION)"/g' src/powermem/core/telemetry.py
	@# Update src/powermem/core/audit.py
	@sed -i 's/"version": "0\.[0-9]\+\.[0-9]\+"/"version": "$(VERSION)"/g' src/powermem/core/audit.py
	@# Update examples/langgraph/requirements.txt
	@sed -i 's/powermem>=0\.[0-9]\+\.[0-9]\+/powermem>=$(VERSION)/' examples/langgraph/requirements.txt
	@# Update examples/langchain/requirements.txt
	@sed -i 's/powermem>=0\.[0-9]\+\.[0-9]\+/powermem>=$(VERSION)/' examples/langchain/requirements.txt
	@echo "✓ Version updated to $(VERSION) in all files"
	@echo ""
	@echo "Updated files:"
	@echo "  - pyproject.toml"
	@echo "  - src/powermem/version.py"
	@echo "  - examples/langgraph/__init__.py"
	@echo "  - src/powermem/core/telemetry.py"
	@echo "  - src/powermem/core/audit.py"
	@echo "  - examples/langgraph/requirements.txt"
	@echo "  - examples/langchain/requirements.txt"
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
SERVER_PORT := $(or $(ENV_SERVER_PORT),8000)
SERVER_WORKERS := $(or $(ENV_SERVER_WORKERS),4)

server-start: ## Start the PowerMem API server
	@echo "Starting PowerMem API server..."
	@if [ -f $(SERVER_PID_FILE) ]; then \
		echo "Server is already running (PID: $$(cat $(SERVER_PID_FILE)))"; \
		echo "Use 'make server-stop' to stop it first, or 'make server-restart' to restart"; \
		exit 1; \
	fi
	@powermem-server --host $(SERVER_HOST) --port $(SERVER_PORT) --workers $(SERVER_WORKERS) > /dev/null 2>&1 & \
	echo $$! > $(SERVER_PID_FILE); \
	echo "Server started with PID: $$!"; \
	echo "Server running at http://$(SERVER_HOST):$(SERVER_PORT)"; \
	echo "API docs available at http://$(SERVER_HOST):$(SERVER_PORT)/docs"; \
	echo "Logs are being written to server.log (configured via POWERMEM_SERVER_LOG_FILE)"; \
	echo "Use 'make server-stop' to stop the server"

server-start-reload: ## Start the PowerMem API server with auto-reload (development mode)
	@echo "Starting PowerMem API server with auto-reload..."
	@if [ -f $(SERVER_PID_FILE) ]; then \
		echo "Server is already running (PID: $$(cat $(SERVER_PID_FILE)))"; \
		echo "Use 'make server-stop' to stop it first"; \
		exit 1; \
	fi
	@powermem-server --host $(SERVER_HOST) --port $(SERVER_PORT) --reload > /dev/null 2>&1 & \
	echo $$! > $(SERVER_PID_FILE); \
	echo "Server started with PID: $$! (auto-reload enabled)"; \
	echo "Server running at http://$(SERVER_HOST):$(SERVER_PORT)"; \
	echo "API docs available at http://$(SERVER_HOST):$(SERVER_PORT)/docs"; \
	echo "Logs are being written to server.log (configured via POWERMEM_SERVER_LOG_FILE)"; \
	echo "Use 'make server-stop' to stop the server"

server-stop: ## Stop the PowerMem API server
	@if [ ! -f $(SERVER_PID_FILE) ]; then \
		echo "Server PID file not found. Checking for running processes..."; \
		PID=$$(lsof -t -i:$(SERVER_PORT) 2>/dev/null || echo ""); \
		if [ -z "$$PID" ]; then \
			echo "No server process found on port $(SERVER_PORT)"; \
			exit 0; \
		else \
			echo "Found process $$PID on port $(SERVER_PORT), stopping..."; \
			kill $$PID 2>/dev/null || kill -9 $$PID 2>/dev/null; \
			echo "Server stopped"; \
			exit 0; \
		fi; \
	fi
	@PID=$$(cat $(SERVER_PID_FILE) 2>/dev/null || echo ""); \
	if [ -z "$$PID" ]; then \
		echo "PID file exists but is empty"; \
		rm -f $(SERVER_PID_FILE); \
		exit 0; \
	fi; \
	if ps -p $$PID > /dev/null 2>&1; then \
		echo "Stopping server (PID: $$PID)..."; \
		kill $$PID 2>/dev/null || kill -9 $$PID 2>/dev/null; \
		sleep 1; \
		if ps -p $$PID > /dev/null 2>&1; then \
			echo "Force killing server (PID: $$PID)..."; \
			kill -9 $$PID 2>/dev/null; \
		fi; \
		echo "Server stopped"; \
	else \
		echo "Server process (PID: $$PID) not found, cleaning up PID file"; \
	fi; \
	rm -f $(SERVER_PID_FILE); \
	echo "✓ Server stopped"

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

# Docker commands
DOCKER_IMAGE := oceanbase/powermem-server
DOCKER_TAG := latest
DOCKER_COMPOSE_FILE := docker/docker-compose.yml

docker-build: ## Build Docker image
	@echo "Building Docker image $(DOCKER_IMAGE):$(DOCKER_TAG)..."
	docker build -t $(DOCKER_IMAGE):$(DOCKER_TAG) -f docker/Dockerfile .
	@echo "✓ Docker image built successfully"

docker-build-mirror: ## Build Docker image with pip mirror source (usage: make docker-build-mirror MIRROR=tsinghua)
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
		-p 8000:8000 \
		-v $$(pwd)/.env:/app/.env:ro \
		--env-file .env \
		$(DOCKER_IMAGE):$(DOCKER_TAG) || \
		(echo "Container may already exist. Use 'make docker-stop' first or 'make docker-restart'"; exit 1)
	@echo "✓ Container started"
	@echo "Server running at http://localhost:8000"
	@echo "API docs at http://localhost:8000/docs"

docker-up: ## Start services using docker-compose
	@echo "Starting services with docker-compose..."
	docker-compose -f $(DOCKER_COMPOSE_FILE) up -d
	@echo "✓ Services started"
	@echo "Server running at http://localhost:8000"
	@echo "API docs at http://localhost:8000/docs"

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
