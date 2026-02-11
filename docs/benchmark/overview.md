# PowerMem Benchmark

A comprehensive benchmarking suite for PowerMem, including a REST API server for managing memories and a load testing tool based on the LOCOMO dataset.


## Overview

The PowerMem Benchmark suite consists of two main components:

1. **Benchmark Server** (`benchmark/server/`): A FastAPI-based REST API server that provides:
   - Memory storage and management
   - Semantic search capabilities
   - Token usage tracking
   - Support for multiple database backends (OceanBase, PostgreSQL)

2. **Load Testing Tool** (`benchmark/locomo/`): A comprehensive benchmarking tool that:
   - Tests memory addition and search performance
   - Evaluates response quality using multiple metrics
   - Measures latency and token consumption
   - Uses the LOCOMO dataset for realistic testing scenarios

## Quick Start

### 1. Start the Benchmark Server

```bash
# Install dependencies
pip install -e .

# Configure environment
cp benchmark/server/.env.example benchmark/server/.env
# Edit benchmark/server/.env with your settings

# Start the server
uvicorn benchmark.server.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Run Load Tests

```bash
# Install load testing dependencies
pip install -r benchmark/locomo/requirements.txt

# Configure environment
cd benchmark/locomo
cp .env.example .env
# Edit .env with your API keys and server URL

# Run tests
bash run.sh results
```

## Benchmark Server

### Prerequisites

- Python 3.10 or higher
- pip or poetry for dependency management
- Access to OpenAI API (or compatible API endpoint)
- Database: OceanBase or PostgreSQL (depending on your configuration)

### Installation

1. **Install dependencies**

   From the project root:
   ```bash
   pip install -e .
   ```

   Or install specific dependencies:
   ```bash
   pip install fastapi uvicorn python-dotenv powermem
   ```

2. **Configure environment variables**

   Copy the example environment file:
   ```bash
   cp benchmark/server/.env.example benchmark/server/.env
   ```

   Edit `benchmark/server/.env` and configure:
   - `OPENAI_API_KEY`: Your OpenAI API key (required)
   - `EMBEDDER_API_KEY`: Optional, separate API key for embeddings (defaults to `OPENAI_API_KEY`)
   - Database configuration (OceanBase or PostgreSQL)
   - Other settings as needed

   See `benchmark/server/.env.example` for all available configuration options.

### Configuration

All configuration is done through environment variables. The server automatically loads a `.env` file from the `benchmark/server/` directory.

#### Required Environment Variables

- `OPENAI_API_KEY`: Your OpenAI API key

#### Optional Environment Variables

- `EMBEDDER_API_KEY`: Separate API key for embeddings (defaults to `OPENAI_API_KEY`)
- `DB_TYPE`: Database type - `oceanbase` or `postgres` (default: `oceanbase`)
- `TOKEN_COUNTING`: Enable token counting - `true` or `false` (default: `true`)
- `LLM_MODEL`: LLM model name (default: `gpt-4o`)
- `LLM_TEMPERATURE`: LLM temperature (default: `0.2`)
- `EMBEDDER_MODEL`: Embedding model name (default: `text-embedding-3-small`)
- `EMBEDDER_DIMS`: Embedding dimensions (default: `1536`)

For database-specific configuration, see `benchmark/server/.env.example`.

### Starting the Server

#### Method 1: Using uvicorn (Recommended)

From the project root:
```bash
uvicorn benchmark.server.main:app --host 0.0.0.0 --port 8000 --reload
```

The `--reload` flag enables auto-reload during development.

#### Method 2: Production Mode

For production with multiple workers:
```bash
uvicorn benchmark.server.main:app --host 0.0.0.0 --port 8000 --workers 4
```

#### Method 3: Using Python Module

```bash
python -m uvicorn benchmark.server.main:app --host 0.0.0.0 --port 8000
```

#### Method 4: From Server Directory

If you're in the `benchmark/server` directory:
```bash
cd benchmark/server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Accessing the API

Once the server is running, you can access:

- **Alternative API Documentation (ReDoc)**: http://localhost:8000/redoc
- **API Root**: http://localhost:8000/

### API Endpoints

The server provides the following main endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/memories` | Create new memories |
| `GET` | `/memories` | Get all memories (with filters) |
| `GET` | `/memories/{memory_id}` | Get a specific memory |
| `PUT` | `/memories/{memory_id}` | Update a memory |
| `DELETE` | `/memories/{memory_id}` | Delete a memory |
| `DELETE` | `/memories` | Delete all memories (with filters) |
| `POST` | `/search` | Search memories |
| `GET` | `/memories/{memory_id}/history` | Get memory history |
| `POST` | `/reset` | Reset all memories |
| `POST` | `/configure` | Update server configuration |
| `GET` | `/token_count` | Get token usage statistics |
| `POST` | `/reset_token_count` | Reset token count |


## Load Testing (LOCOMO)

The LOCOMO benchmark tool performs comprehensive evaluations of memory systems using the LOCOMO dataset. It tests both memory addition and search capabilities, measuring performance, quality, and resource consumption.

### Prerequisites for Load Testing

1. **Benchmark server must be running**
   - Start the benchmark server first (see [Starting the Server](#starting-the-server))
   - The server should be accessible at the URL specified in your configuration

2. **Install LOCOMO dependencies**

   From the project root:
   ```bash
   pip install -r benchmark/locomo/requirements.txt
   ```

   Or from the locomo directory:
   ```bash
   cd benchmark/locomo
   pip install -r requirements.txt
   ```

### Configuring Environment Variables for Load Testing

1. **Create environment configuration file**

   ```bash
   cd benchmark/locomo
   cp .env.example .env
   ```

2. **Edit the `.env` file**

   Open `benchmark/locomo/.env` and configure the following variables:

   ```bash
   # OpenAI API configuration
   MODEL="qwen3-max"  # or "gpt-4o", "gpt-4", etc.
   OPENAI_BASE_URL="https://api.openai.com/v1"  # or your API endpoint
   OPENAI_API_KEY="your_api_key_here"  # Your OpenAI API key

   # API configuration - must match your running server
   API_BASE_URL="http://127.0.0.1:8000"  # URL of the benchmark server
   ```

   **Important Configuration Notes:**
   - `API_BASE_URL`: Must match the URL where your benchmark server is running
     - Default: `http://127.0.0.1:8000` (if server runs on localhost:8000)
     - If server runs on a different port, update accordingly
   - `MODEL`: The LLM model to use for answer generation
   - `OPENAI_API_KEY`: API key for the LLM service
   - `OPENAI_BASE_URL`: Base URL for the LLM API (can be OpenAI or compatible service)

### Running Load Tests

#### Full Test Suite

1. **Ensure the benchmark server is running**

   In a separate terminal:
   ```bash
   uvicorn benchmark.server.main:app --host 0.0.0.0 --port 8000
   ```

2. **Run the complete test script**

   From the `benchmark/locomo` directory:
   ```bash
   cd benchmark/locomo
   bash run.sh [output_folder]
   ```

   Or from the project root:
   ```bash
   cd benchmark/locomo && bash run.sh results
   ```

   The `output_folder` parameter is optional (defaults to `results`).

3. **What the script does**

   The `run.sh` script performs the following steps:
   - Resets token count on the server
   - Records initial token count
   - Runs memory addition experiments (`add` method)
   - Runs memory search experiments (`search` method)
   - Records final token count
   - Generates evaluation metrics
   - Displays evaluation results

#### Running Individual Test Methods

You can also run individual test methods manually:

**Memory Addition Test:**
```bash
cd benchmark/locomo
python3 run_experiments.py --method add --output_folder results
```

**Memory Search Test:**
```bash
cd benchmark/locomo
python3 run_experiments.py --method search --output_folder results --top_k 30
```

**Available Options:**
- `--method`: Test method - `add` or `search` (default: `add`)
- `--chunk_size`: Chunk size for processing (default: 1000)
- `--top_k`: Number of top memories to retrieve (default: 30)
- `--filter_memories`: Enable memory filtering
- `--is_graph`: Use graph-based search
- `--num_chunks`: Number of chunks to process (default: 1)
- `--output_folder`: Output directory for results (default: `results/`)

### Understanding Test Results

After running the tests, results are stored in the output folder (default: `results/`):

#### Output Files

- **`results.json`**: Detailed results for each question and conversation
  - Contains all question-answer pairs with retrieved memories
  - Includes timing information for each operation
  
- **`evaluation_metrics.json`**: Computed evaluation metrics
  - BLEU scores
  - F1 scores
  - LLM judge scores
  
- **`evaluation.txt`**: Human-readable evaluation summary
  - Total server execution time
  - Total requests processed
  - Average request time
  - P95 latency (95th percentile)
  - Token consumption statistics
  
- **`token1.json`** and **`token2.json`**: Token counts before and after tests
  - Used to calculate total token consumption

### Evaluation Metrics

The benchmark evaluates performance using multiple metrics:

1. **BLEU Score**: Measures similarity between model response and ground truth
   - Range: 0.0 to 1.0 (higher is better)
   - Based on n-gram overlap

2. **F1 Score**: Harmonic mean of precision and recall
   - Range: 0.0 to 1.0 (higher is better)
   - Measures answer quality

3. **LLM Score**: Binary score (0 or 1) from LLM judge evaluation
   - 1 = Correct answer, 0 = Incorrect answer
   - Determined by an LLM judge

4. **Token Consumption**: Number of tokens used for answer generation
   - Includes both prompt and completion tokens
   - Tracked before and after tests

5. **Latency Metrics**:
   - **Average request time**: Mean time per request
   - **P95 latency**: 95th percentile request time
   - **Total execution time**: Total time for all operations

## Troubleshooting

### Server Issues

#### "OPENAI_API_KEY environment variable is required"
- **Solution**: Create a `.env` file in `benchmark/server/` directory
- Verify that `OPENAI_API_KEY` is set in the `.env` file
- Check that the file is being loaded (server logs will show the path)

#### Database connection errors
- **Solution**: 
  - Check your database configuration in `.env`
  - Ensure your database server is running
  - Verify connection credentials (host, port, user, password)
  - Test database connectivity separately

#### Port already in use
- **Solution**: 
  - Change the port: `uvicorn benchmark.server.main:app --port 8001`
  - Or find and kill the process using the port:
    ```bash
    lsof -ti:8000 | xargs kill -9
    ```

#### OpenAI API 404 Not Found Error
- **Solution**:
  - Check your `OPENAI_BASE_URL` is right, for example:
    - https://api.openai.com/v1 ✅
    - https://api.openai.com ❌
    - https://api.openai.com/v1/chat/completions ❌
    - https://api.openai.com/v1/embeddings ❌

#### OpenAI API 402 Rate Limiting
- **Solution**:
  - Reduce concurrency: Adjust `max_workers` in `methods/add.py` if needed
  - Increase the number of ApiKeys (the multi-ApiKey proxy solution will be uploaded in the future)

#### Module not found errors
- **Solution**: 
  - Install dependencies: `pip install -e .`
  - Verify you're running from the project root directory
  - Check Python path and virtual environment activation

### Load Testing Issues

#### "api_base_url is not set"
- **Solution**: 
  - Create `.env` file in `benchmark/locomo/` directory
  - Verify that `API_BASE_URL` is set correctly
  - Ensure the URL matches your running server address

#### Connection refused errors
- **Solution**: 
  - Verify the benchmark server is running
  - Check that `API_BASE_URL` in `.env` matches the server URL
  - Ensure the server is accessible from the test location

#### "model is not set" or "openai_api_key is not set"
- **Solution**: 
  - Check that `MODEL` and `OPENAI_API_KEY` are set in `benchmark/locomo/.env`
  - Verify the API key is valid
  - Ensure no extra quotes or spaces in the values

#### Import errors
- **Solution**: 
  - Install all dependencies: `pip install -r benchmark/locomo/requirements.txt`
  - Ensure you're running from the correct directory
  - Check Python version (requires 3.10+)

#### Slow performance
- **Solution**: 
  - The tests use multi-threading (32 workers by default)
  - Reduce concurrency: Adjust `max_workers` in `methods/add.py` if needed
  - Consider running tests on a machine with more resources
  - Check server performance and database connection pool size

#### Dataset not found
- **Solution**: 
  - Nltk data not found:
    ```python
    import nltk
    nltk.download("punkt", quiet=True)
    nltk.download("wordnet", quiet=True)
    ```
  - SentenceTransformer model not found:
    ```python
    from sentence_transformers import SentenceTransformer
    # Initialize SentenceTransformer model (this will be reused)
    sentence_model = SentenceTransformer("all-MiniLM-L6-v2")
    ```

## License

See the main project LICENSE file for details.
