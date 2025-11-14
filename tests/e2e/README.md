# End-to-End Tests

This directory contains end-to-end tests for powermem.

## Test Categories

### Standard E2E Tests (`test_e2e_scenarios.py`)

These tests use mocked providers and are included in the default test suite.

Run with:
```bash
make test-e2e
# or
pytest tests/e2e/ -v -m "e2e and not e2e_config"
```

### Configuration-Based E2E Tests

These tests require real configuration files and are **NOT** included in the default test suite.

Test files:
- `test_basic_usage_e2e.py` - Basic memory operations
- `test_agent_memory_e2e.py` - Agent memory scenarios
- `test_intelligent_memory_e2e.py` - Intelligent memory scenarios

Run with:
```bash
make test-e2e-config
# or
pytest tests/e2e/ -v -m e2e_config
```

## Setup

Before running configuration-based E2E tests, you need to set up your configuration:

1. **Copy the example configuration:**
   ```bash
   cp .env.example .env
   ```

2. **Edit the configuration file** (`.env`) with your actual credentials:
   - Database connection details (OceanBase or SQLite)
   - LLM API keys (Qwen, OpenAI, etc.)
   - Embedding API keys

3. **Verify the configuration file exists:**
   ```bash
   ls .env
   ```

## Configuration File Location

The tests will look for configuration files in this order:

1. `.env` (preferred)
2. `.env` in the project root
3. Any `.env` file found by `python-dotenv`

## Running Tests

### Run all E2E tests (excluding config-based):
```bash
make test-e2e
```

### Run only config-based E2E tests:
```bash
make test-e2e-config
```

### Run a specific config-based test file:
```bash
pytest tests/e2e/test_basic_usage_e2e.py -v -m e2e_config
pytest tests/e2e/test_agent_memory_e2e.py -v -m e2e_config
pytest tests/e2e/test_intelligent_memory_e2e.py -v -m e2e_config
```

### Run a specific test:
```bash
pytest tests/e2e/test_basic_usage_e2e.py::TestBasicUsageE2E::test_basic_memory_operations -v -m e2e_config
```

## Notes

- These tests use **real** database connections and API calls
- They may take longer to run than mocked tests
- Make sure you have valid API keys and database access
- Tests will clean up test data after running, but be aware they may create temporary data
- These tests are excluded from CI/CD pipelines by default

## Troubleshooting

### Configuration not found
If you see errors about missing configuration:
1. Check that `.env` exists
2. Verify the file has valid environment variables
3. Check file permissions

### Database connection errors
- Verify your database is running and accessible
- Check connection credentials in the config file
- For OceanBase, ensure the host and port are correct

### API key errors
- Verify your API keys are valid
- Check that the API keys have sufficient quota
- Ensure the API endpoints are accessible from your network

