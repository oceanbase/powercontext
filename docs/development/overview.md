# Development Guide

This guide provides comprehensive instructions for developers who want to contribute to PowerMem, including how to set up the development environment, build the project, and add new integrations.

## Table of Contents

- [Development Environment Setup](#development-environment-setup)
- [Building the Project](#building-the-project)
- [Contributing Code](#contributing-code)
- [Adding a Vector Store](#adding-a-vector-store)
- [Adding an LLM Provider](#adding-an-llm-provider)
- [Adding an Embedding Provider](#adding-an-embedding-provider)
- [Adding a Reranker Provider](#adding-a-reranker-provider)
- [Testing](#testing)
- [Code Style and Standards](#code-style-and-standards)
- [Publishing to PyPI](#publishing-to-pypi)
- [Debugging](#debugging)
- [Performance Optimization](#performance-optimization)
- [Common Issues and Solutions](#common-issues-and-solutions)
- [Advanced Topics](#advanced-topics)
- [Additional Resources](#additional-resources)
- [Getting Help](#getting-help)

## Development Environment Setup

### Prerequisites

- Python 3.10 or higher
- pip (Python package manager)
- Git

### Installation

1. **Clone the repository:**

```bash
git clone https://github.com/oceanbase/powermem.git
cd powermem
```

2. **Install development dependencies:**

```bash
# Install the project in development mode with all dependencies
make install-dev

# Or install with test dependencies
make install-test
```

Alternatively, you can use pip directly:

```bash
pip install -e ".[dev,test]"
```

3. **Set up environment variables:**

Copy the example environment file and configure it:

```bash
cp .env.example .env
# Edit .env with your configuration
```

### Development Tools

The project uses several development tools that are installed with `dev` dependencies:

- **pytest**: Testing framework
- **black**: Code formatter
- **isort**: Import sorter
- **flake8**: Linter
- **mypy**: Type checker

## Building the Project

### Build Distribution Packages

To build wheel and source distribution packages:

```bash
make build-package
```

This will:
- Clean previous build artifacts
- Build both wheel (`.whl`) and source distribution (`.tar.gz`) packages
- Output files to the `dist/` directory

### Check Built Package

Before publishing, verify the package:

```bash
make build-check
```

This runs `twine check` to validate the package.

### Install Build Tools

If you need to install build tools manually:

```bash
make install-build-tools
```

This installs `build` and `twine` packages.

### Install Locally

To install the built package locally:

```bash
make install-local
```

This installs the wheel package from `dist/` directory.

## Contributing Code

### Workflow

1. **Fork the repository** on GitHub
2. **Create a feature branch:**

```bash
git checkout -b feature/your-feature-name
```

3. **Make your changes** following the code style guidelines
4. **Write tests** for your changes
5. **Run tests** to ensure everything passes:

```bash
make test
```

6. **Format and lint your code:**

```bash
make format
make lint
```

7. **Commit your changes** with clear commit messages
8. **Push to your fork** and create a Pull Request

### Commit Message Guidelines

- Use clear, descriptive commit messages
- Start with a verb in imperative mood (e.g., "Add", "Fix", "Update")
- Reference issue numbers when applicable (e.g., "Fix #123: Add error handling")

### Pull Request Guidelines

- Provide a clear description of changes
- Include tests for new features
- Update documentation if needed
- Ensure all tests pass
- Follow the code style guidelines

## Adding a Vector Store

To add a new vector store provider, you need to:

1. **Create the vector store implementation**
2. **Create the configuration class**
3. **Register it in the factory**

### Step 1: Create Vector Store Implementation

Create a new file in `src/powermem/storage/your_provider/your_provider.py`:

```python
"""
YourProvider vector store implementation
"""
import logging
from typing import Any, Dict, List, Optional

from powermem.storage.base import VectorStoreBase, OutputData
from powermem.utils.utils import generate_snowflake_id

logger = logging.getLogger(__name__)


class YourProviderVectorStore(VectorStoreBase):
    """YourProvider vector store implementation"""
    
    def __init__(
        self,
        collection_name: str = "memories",
        # Add your provider-specific parameters
        host: Optional[str] = None,
        port: Optional[int] = None,
        **kwargs
    ):
        """
        Initialize the vector store.
        
        Args:
            collection_name: Name of the collection
            host: Server host
            port: Server port
        """
        self.collection_name = collection_name
        # Initialize your provider client here
        logger.info(f"YourProviderVectorStore initialized")
    
    def create_col(self, name=None, vector_size=None, distance="cosine"):
        """Create a new collection."""
        collection_name = name or self.collection_name
        # Implement collection creation logic
        pass
    
    def insert(self, vectors: List[List[float]], payloads=None, ids=None) -> List[int]:
        """
        Insert vectors into the collection.
        
        Args:
            vectors: List of vectors to insert
            payloads: List of payload dictionaries
            ids: Optional list of IDs (if None, will generate Snowflake IDs)
            
        Returns:
            List[int]: List of generated or provided IDs
        """
        if not vectors:
            return []
        
        if payloads is None:
            payloads = [{} for _ in vectors]
        
        # Generate IDs if not provided
        if ids is None:
            ids = [generate_snowflake_id() for _ in range(len(vectors))]
        
        # Implement insertion logic
        # ...
        
        return ids
    
    def search(
        self, 
        query: List[float], 
        vectors: Optional[List[List[float]]] = None, 
        limit: int = 5, 
        filters: Optional[Dict[str, Any]] = None
    ) -> List[OutputData]:
        """
        Search for similar vectors.
        
        Args:
            query: Query vector
            vectors: Optional list of vectors to search in
            limit: Maximum number of results
            filters: Optional metadata filters
            
        Returns:
            List[OutputData]: List of search results
        """
        # Implement search logic
        results = []
        # ...
        return results
    
    def delete(self, vector_id: int) -> bool:
        """Delete a vector by ID."""
        # Implement deletion logic
        return True
    
    def update(self, vector_id: int, vector=None, payload=None) -> bool:
        """Update a vector and its payload."""
        # Implement update logic
        return True
    
    def get(self, vector_id: int) -> Optional[OutputData]:
        """Retrieve a vector by ID."""
        # Implement retrieval logic
        return None
    
    def list_cols(self) -> List[str]:
        """List all collections."""
        # Implement list logic
        return []
    
    def delete_col(self, name=None) -> bool:
        """Delete a collection."""
        # Implement deletion logic
        return True
    
    def col_info(self) -> Dict[str, Any]:
        """Get information about a collection."""
        # Implement info retrieval
        return {}
    
    def list(self, filters=None, limit=None) -> List[OutputData]:
        """List all memories."""
        # Implement list logic
        return []
    
    def reset(self) -> bool:
        """Reset by deleting the collection and recreating it."""
        self.delete_col()
        self.create_col()
        return True
```

### Step 2: Create Configuration Class

Create a configuration file in `src/powermem/storage/config/your_provider.py`:

```python
"""
Configuration for YourProvider vector store
"""
from typing import Optional
from pydantic import Field

from powermem.storage.config.base import BaseVectorStoreConfig


class YourProviderConfig(BaseVectorStoreConfig):
    """Configuration for YourProvider vector store"""
    
    host: str = Field(description="Server host")
    port: int = Field(default=5432, description="Server port")
    # Add other provider-specific fields
    
    class Config:
        extra = "allow"
```

Update `src/powermem/storage/configs.py` to include your config:

```python
from powermem.storage.config.your_provider import YourProviderConfig

class VectorStoreConfig(BaseModel):
    # ...
    _provider_configs: Dict[str, str] = {
        "oceanbase": "OceanBaseConfig",
        "pgvector": "PGVectorConfig",
        "sqlite": "SQLiteConfig",
        "your_provider": "YourProviderConfig",  # Add this
    }
```

### Step 3: Register in Factory

Update `src/powermem/storage/factory.py`:

```python
class VectorStoreFactory:
    provider_to_class = {
        "oceanbase": "powermem.storage.oceanbase.oceanbase.OceanBaseVectorStore",
        "sqlite": "powermem.storage.sqlite.sqlite_vector_store.SQLiteVectorStore",
        "pgvector": "powermem.storage.pgvector.pgvector.PGVectorStore",
        "postgres": "powermem.storage.pgvector.pgvector.PGVectorStore",
        "your_provider": "powermem.storage.your_provider.your_provider.YourProviderVectorStore",  # Add this
    }
```

### Step 4: Add Tests

Create test file `tests/integration/test_your_provider_vector_store.py`:

```python
import pytest
from powermem.storage.factory import VectorStoreFactory


def test_your_provider_create_col():
    """Test collection creation"""
    config = {
        "collection_name": "test_collection",
        "host": "localhost",
        "port": 5432,
    }
    store = VectorStoreFactory.create("your_provider", config)
    store.create_col("test_col", vector_size=128, distance="cosine")
    assert store.col_info() is not None


def test_your_provider_insert_and_search():
    """Test insertion and search"""
    config = {
        "collection_name": "test_collection",
        "host": "localhost",
        "port": 5432,
    }
    store = VectorStoreFactory.create("your_provider", config)
    store.create_col("test_col", vector_size=128, distance="cosine")
    
    vectors = [[0.1] * 128, [0.2] * 128]
    payloads = [{"text": "test1"}, {"text": "test2"}]
    ids = store.insert(vectors, payloads)
    
    assert len(ids) == 2
    
    results = store.search([0.1] * 128, limit=1)
    assert len(results) > 0
```

## Adding an LLM Provider

To add a new LLM provider:

1. **Create the LLM implementation**
2. **Create the configuration class**
3. **Register it in the factory**

### Step 1: Create LLM Implementation

Create `src/powermem/integrations/llm/your_provider.py`:

```python
"""
YourProvider LLM implementation
"""
from typing import Dict, List, Optional

from powermem.integrations.llm import LLMBase
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.integrations.llm.config.your_provider import YourProviderConfig

# Import your provider's SDK
try:
    from your_provider_sdk import YourProviderClient
except ImportError:
    raise ImportError(
        "The 'your_provider_sdk' library is required. "
        "Please install it using 'pip install your_provider_sdk'."
    )


class YourProviderLLM(LLMBase):
    """YourProvider LLM implementation"""
    
    def __init__(self, config: Optional[YourProviderConfig] = None):
        if config is None:
            config = YourProviderConfig()
        elif isinstance(config, dict):
            config = YourProviderConfig(**config)
        
        super().__init__(config)
        
        # Initialize your provider client
        self.client = YourProviderClient(
            api_key=self.config.api_key,
            base_url=getattr(self.config, 'base_url', None),
        )
    
    def generate_response(
        self,
        messages: List[Dict[str, str]],
        response_format=None,
        tools: Optional[List[Dict]] = None,
        tool_choice: str = "auto",
        **kwargs,
    ) -> str:
        """
        Generate a response based on the given messages.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            response_format: Optional response format specification
            tools: Optional list of tools for function calling
            tool_choice: Tool choice strategy
            **kwargs: Additional parameters
            
        Returns:
            str: Generated response text
        """
        # Prepare parameters
        params = self._get_supported_params(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs
        )
        
        # Add provider-specific parameters
        params.update({
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        })
        
        # Call your provider's API
        response = self.client.chat.completions.create(**params)
        
        # Parse and return response
        return self._parse_response(response, tools)
    
    def _parse_response(self, response, tools=None):
        """Parse the response from the provider"""
        if tools:
            # Handle tool calls if supported
            return {
                "content": response.choices[0].message.content,
                "tool_calls": getattr(response.choices[0].message, 'tool_calls', []),
            }
        else:
            return response.choices[0].message.content
```

### Step 2: Create Configuration Class

Create `src/powermem/integrations/llm/config/your_provider.py`:

```python
"""
Configuration for YourProvider LLM
"""
from typing import Optional
from pydantic import Field

from powermem.integrations.llm.config.base import BaseLLMConfig


class YourProviderConfig(BaseLLMConfig):
    """Configuration for YourProvider LLM"""
    
    base_url: Optional[str] = Field(
        default=None,
        description="Base URL for the API"
    )
    # Add provider-specific fields
    
    class Config:
        extra = "allow"
```

### Step 3: Register in Factory

Update `src/powermem/integrations/llm/factory.py`:

```python
from powermem.integrations.llm.config.your_provider import YourProviderConfig

class LLMFactory:
    provider_to_class = {
        # ... existing providers ...
        "your_provider": ("powermem.integrations.llm.your_provider.YourProviderLLM", YourProviderConfig),
    }
```

### Step 4: Add to Dependencies

If your provider requires a new dependency, add it to `pyproject.toml`:

```toml
[project]
dependencies = [
    # ... existing dependencies ...
    "your_provider_sdk>=1.0.0",
]
```

## Adding an Embedding Provider

To add a new embedding provider:

1. **Create the embedding implementation**
2. **Register it in the factory**

### Step 1: Create Embedding Implementation

Create `src/powermem/integrations/embeddings/your_provider.py`:

```python
"""
YourProvider embedding implementation
"""
from typing import Literal, Optional, List

from powermem.integrations.embeddings import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig

# Import your provider's SDK
try:
    from your_provider_sdk import YourProviderEmbeddingClient
except ImportError:
    raise ImportError(
        "The 'your_provider_sdk' library is required. "
        "Please install it using 'pip install your_provider_sdk'."
    )


class YourProviderEmbedding(EmbeddingBase):
    """YourProvider embedding implementation"""
    
    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)
        
        # Initialize your provider client
        self.client = YourProviderEmbeddingClient(
            api_key=getattr(self.config, 'api_key', None),
            model=self.config.model,
        )
    
    def embed(
        self, 
        text: str, 
        memory_action: Optional[Literal["add", "search", "update"]] = None
    ) -> List[float]:
        """
        Get the embedding for the given text.
        
        Args:
            text: The text to embed
            memory_action: The type of embedding to use (optional)
            
        Returns:
            List[float]: The embedding vector
        """
        # Some providers support different embedding models for different actions
        model = self.config.model
        if memory_action == "search" and hasattr(self.config, 'search_model'):
            model = self.config.search_model
        
        # Call your provider's API
        response = self.client.embed(text, model=model)
        
        return response.embedding
```

### Step 2: Register in Factory

Update `src/powermem/integrations/embeddings/factory.py`:

```python
class EmbedderFactory:
    provider_to_class = {
        # ... existing providers ...
        "your_provider": "powermem.integrations.embeddings.your_provider.YourProviderEmbedding",
    }
```

## Adding a Reranker Provider

Rerankers improve search results by reordering documents based on relevance to the query. To add a new reranker provider:

1. **Create the reranker implementation**
2. **Register it in the factory**

### Step 1: Create Reranker Implementation

Create `src/powermem/integrations/rerank/your_provider.py`:

```python
"""
YourProvider reranker implementation
"""
import os
from typing import List, Optional, Tuple

from powermem.integrations.rerank.base import RerankBase
from powermem.integrations.rerank.config.base import BaseRerankConfig

# Import your provider's SDK
try:
    from your_provider_sdk import YourProviderRerankClient
except ImportError:
    raise ImportError(
        "The 'your_provider_sdk' library is required. "
        "Please install it using 'pip install your_provider_sdk'."
    )


class YourProviderRerank(RerankBase):
    """YourProvider reranker implementation"""
    
    def __init__(self, config: Optional[BaseRerankConfig] = None):
        super().__init__(config)
        
        # Set default model
        self.config.model = self.config.model or "your-rerank-model"
        
        # Initialize your provider client
        api_key = self.config.api_key or os.getenv("YOUR_PROVIDER_API_KEY")
        if not api_key:
            raise ValueError(
                "API key is required. Set YOUR_PROVIDER_API_KEY environment variable "
                "or pass api_key in config."
            )
        
        self.client = YourProviderRerankClient(api_key=api_key)
    
    def rerank(
        self, 
        query: str, 
        documents: List[str], 
        top_n: Optional[int] = None
    ) -> List[Tuple[int, float]]:
        """
        Rerank documents based on relevance to the query.
        
        Args:
            query: The search query
            documents: List of document texts to rerank
            top_n: Number of top results to return
            
        Returns:
            List[Tuple[int, float]]: List of (document_index, relevance_score) tuples,
                                     sorted by relevance score in descending order
        """
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")
        
        if not documents or len(documents) == 0:
            raise ValueError("Documents list cannot be empty")
        
        # Use provided top_n or return all results
        effective_top_n = top_n if top_n is not None else len(documents)
        
        try:
            # Call your provider's rerank API
            response = self.client.rerank(
                query=query,
                documents=documents,
                model=self.config.model,
                top_n=effective_top_n,
            )
            
            # Parse results - format: [(index, score), ...]
            results = []
            for item in response.results:
                index = item.index
                score = item.relevance_score
                results.append((index, float(score)))
            
            # Sort by score descending (highest first)
            results.sort(key=lambda x: x[1], reverse=True)
            
            return results
            
        except Exception as e:
            raise Exception(f"Failed to rerank documents: {e}")
```

### Step 2: Register in Factory

Update `src/powermem/integrations/rerank/factory.py`:

```python
class RerankFactory:
    provider_to_class = {
        "qwen": "powermem.integrations.rerank.qwen.QwenRerank",
        "your_provider": "powermem.integrations.rerank.your_provider.YourProviderRerank",  # Add this
    }
```

### Step 3: Configure Reranker

Enable reranker in your configuration:

```python
from powermem import Memory

config = {
    "reranker": {
        "enabled": True,
        "provider": "your_provider",
        "config": {
            "model": "your-rerank-model",
            "api_key": "your-api-key",
        },
    },
    # ... other config
}

memory = Memory(config=config)
```

Or via environment variables:

```bash
export RERANKER_ENABLED=true
export RERANKER_PROVIDER=your_provider
export RERANKER_MODEL=your-rerank-model
export RERANKER_API_KEY=your-api-key
```

## Testing

### Running Tests

```bash
# Run all tests
make test

# Run unit tests only
make test-unit

# Run integration tests only
make test-integration

# Run end-to-end tests
make test-e2e

# Run tests with coverage
make test-coverage
```

### Writing Tests

- **Unit tests**: Test individual functions and classes in isolation
- **Integration tests**: Test interactions between components
- **E2E tests**: Test complete workflows

Place tests in:
- `tests/unit/` for unit tests
- `tests/integration/` for integration tests
- `tests/e2e/` for end-to-end tests

### Test Structure

```python
import pytest
from powermem import Memory


def test_feature_name():
    """Test description"""
    # Arrange
    memory = Memory()
    
    # Act
    result = memory.some_method()
    
    # Assert
    assert result is not None
```

## Code Style and Standards

### Formatting

The project uses `black` for code formatting and `isort` for import sorting:

```bash
# Format code
make format

# Check formatting without making changes
make format-check
```

### Linting

```bash
# Run linter
make lint
```

### Type Checking

```bash
# Run type checker
make type-check
```

### Code Style Guidelines

1. **Follow PEP 8** style guide
2. **Use type hints** for function parameters and return values
3. **Write docstrings** for all classes and public methods
4. **Keep functions focused** - one responsibility per function
5. **Use meaningful variable names**
6. **Handle errors appropriately** - use try/except where needed
7. **Add logging** for important operations

### Example Code Style

```python
"""
Module docstring describing the purpose of this module.
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ExampleClass:
    """
    Class docstring describing what this class does.
    
    Args:
        param1: Description of param1
        param2: Description of param2
    """
    
    def __init__(self, param1: str, param2: Optional[int] = None):
        """Initialize the class."""
        self.param1 = param1
        self.param2 = param2
        logger.info(f"Initialized ExampleClass with param1={param1}")
    
    def example_method(self, input_data: Dict[str, str]) -> List[str]:
        """
        Method docstring describing what this method does.
        
        Args:
            input_data: Dictionary containing input data
            
        Returns:
            List of processed strings
            
        Raises:
            ValueError: If input_data is empty
        """
        if not input_data:
            raise ValueError("input_data cannot be empty")
        
        # Implementation here
        return []
```

## Publishing to PyPI

### Prerequisites

Before publishing to PyPI, ensure you have:

1. **PyPI account**: Create an account at [pypi.org](https://pypi.org)
2. **API tokens**: Generate API tokens from your PyPI account settings
3. **Configure credentials**: Set up `~/.pypirc` or use environment variables

### Publishing Process

1. **Update version** in `pyproject.toml`:

```toml
[project]
version = "0.1.1"  # Increment version number
```

2. **Update changelog** (if you maintain one)

3. **Build and check the package**:

```bash
make build-check
```

4. **Test locally**:

```bash
make install-local
# Test the installed package
python -c "import powermem; print(powermem.__version__)"
```

5. **Publish to TestPyPI first** (recommended):

```bash
make publish-testpypi
```

6. **Test installation from TestPyPI**:

```bash
pip install --index-url https://test.pypi.org/simple/ powermem
```

7. **Publish to PyPI**:

```bash
make publish-pypi
```

### Version Management

Follow [Semantic Versioning](https://semver.org/):

- **MAJOR** version (1.0.0): Incompatible API changes
- **MINOR** version (0.1.0): New functionality in a backward compatible manner
- **PATCH** version (0.0.1): Backward compatible bug fixes

### Git Tagging

After publishing, create a git tag:

```bash
git tag -a v0.1.1 -m "Release version 0.1.1"
git push origin v0.1.1
```

## Debugging

### Enable Debug Logging

Set the logging level to DEBUG:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Or set environment variable:

```bash
export LOG_LEVEL=DEBUG
```

### Common Debugging Scenarios

1. **Vector Store Connection Issues**:

```python
from powermem.storage.factory import VectorStoreFactory

# Test connection
config = {
    "collection_name": "test",
    "host": "localhost",
    "port": 2881,
    # ... other config
}
store = VectorStoreFactory.create("oceanbase", config)
print(store.col_info())  # Check if connection works
```

2. **LLM API Issues**:

```python
from powermem.integrations.llm.factory import LLMFactory

# Test LLM connection
config = {
    "model": "gpt-4",
    "api_key": "your-key",
}
llm = LLMFactory.create("openai", config)
response = llm.generate_response([{"role": "user", "content": "test"}])
print(response)
```

3. **Embedding Issues**:

```python
from powermem.integrations.embeddings.factory import EmbedderFactory

# Test embedding
config = {
    "model": "text-embedding-3-small",
    "api_key": "your-key",
}
embedder = EmbedderFactory.create("openai", config)
vector = embedder.embed("test text")
print(f"Vector dimension: {len(vector)}")
```

## Performance Optimization

### Vector Store Optimization

1. **Index Configuration**:

```python
# For OceanBase
config = {
    "index_type": "HNSW",  # Use HNSW for better performance
    "vidx_metric_type": "l2",  # Choose appropriate metric
    # ... other config
}
```

2. **Batch Operations**:

```python
# Insert multiple vectors at once
vectors = [[0.1] * 1536 for _ in range(100)]
payloads = [{"text": f"text_{i}"} for i in range(100)]
ids = store.insert(vectors, payloads)  # Batch insert
```

3. **Connection Pooling**:

```python
# Use connection pooling for better performance
config = {
    "pool_size": 10,
    "max_overflow": 20,
    # ... other config
}
```

### Memory Optimization

1. **Use Async Operations**:

```python
from powermem import AsyncMemory

memory = AsyncMemory()
# Async operations are more efficient for I/O-bound tasks
await memory.add("text", user_id="user1")
```

2. **Batch Memory Operations**:

```python
# Add multiple memories efficiently
memories = [
    "User likes Python",
    "User works at tech company",
    "User prefers coffee over tea",
]
for mem in memories:
    memory.add(mem, user_id="user1")
```

## Common Issues and Solutions

### Issue: Import Errors

**Problem**: `ModuleNotFoundError` when importing provider-specific modules

**Solution**: Install the required dependencies:

```bash
# For specific providers
pip install pyobvector  # For OceanBase
pip install pgvector    # For PostgreSQL
pip install dashscope    # For Qwen
```

### Issue: Configuration Not Loading

**Problem**: Configuration from `.env` file not being loaded

**Solution**: Ensure the file path is correct:

```python
from powermem import create_memory

# Explicitly specify config file
memory = create_memory(config_file=".env")
```

### Issue: Vector Dimension Mismatch

**Problem**: `ValueError: Vector dimension mismatch`

**Solution**: Ensure embedding model dimensions match vector store configuration:

```python
# Check embedding dimensions
embedder = EmbedderFactory.create("openai", {"model": "text-embedding-3-small"})
vector = embedder.embed("test")
print(f"Dimension: {len(vector)}")  # Should be 1536 for text-embedding-3-small

# Configure vector store with matching dimensions
store_config = {
    "embedding_model_dims": 1536,  # Must match embedding dimension
    # ... other config
}
```

### Issue: Connection Timeout

**Problem**: Timeout errors when connecting to vector store

**Solution**: Increase timeout and check network:

```python
config = {
    "host": "localhost",
    "port": 2881,
    "connect_timeout": 30,  # Increase timeout
    # ... other config
}
```

## Advanced Topics

### Custom Fact Extraction

You can customize how facts are extracted from messages:

```python
from powermem import Memory

custom_prompt = """
Extract key facts from the following conversation.
Focus on: user preferences, important events, relationships.
"""

memory = Memory(
    config={
        "custom_fact_extraction_prompt": custom_prompt,
        # ... other config
    }
)
```

### Custom Memory Update Logic

Customize how memories are updated:

```python
custom_update_prompt = """
Update existing memories based on new information.
Merge similar memories and remove outdated ones.
"""

memory = Memory(
    config={
        "custom_update_memory_prompt": custom_update_prompt,
        # ... other config
    }
)
```

### Sub-Stores Configuration

Configure sub-stores for better data organization:

```python
config = {
    "storage_type": "oceanbase",
    "sub_stores": [
        {
            "name": "user_preferences",
            "filters": {"type": "preference"},
        },
        {
            "name": "events",
            "filters": {"type": "event"},
        },
    ],
    # ... other config
}
memory = Memory(config=config)
```

### Graph Store Integration

Enable graph store for relationship-based retrieval:

```python
config = {
    "graph_store": {
        "provider": "oceanbase",
        "config": {
            # Graph store configuration
        },
    },
    # ... other config
}
memory = Memory(config=config)
```

## Additional Resources

- **API Documentation**: See `docs/api/` for detailed API reference
- **Examples**: Check `examples/` directory for usage examples
- **Architecture**: See `docs/architecture/overview.md` for system architecture
- **Issues**: Report bugs or request features on [GitHub Issues](https://github.com/oceanbase/powermem/issues)

## Getting Help

- **Discord**: Join our [Discord community](https://discord.com/invite/74cF8vbNEs)
- **GitHub Discussions**: Ask questions in [GitHub Discussions](https://github.com/oceanbase/powermem/discussions)
- **Documentation**: Browse the [documentation](https://powermem.readthedocs.io)

---

Happy coding! 🚀