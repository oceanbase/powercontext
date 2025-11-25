# Scenario 5: Custom Integration

This scenario demonstrates how to integrate powermem with custom systems, implement custom providers, and extend functionality.

## Prerequisites

- Completed previous scenarios
- Understanding of Python classes and interfaces
- Basic knowledge of LLM/embedding providers

## Understanding Custom Integration

powermem is designed to be extensible:
- Custom LLM providers
- Custom embedding providers
- Custom storage backends
- Custom intelligence plugins

## Provider and Factory Configuration

Factory configuration with config classes:

```python
from pydantic import Field
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig
from powermem.storage.config.base import BaseVectorStoreConfig

class CustomLLMConfig(BaseLLMConfig):
    base_url: str | None = Field(default=None)
    class Config:
        extra = 'allow'

class CustomEmbedderConfig(BaseEmbedderConfig):
    dims: int = Field(default=768)
    class Config:
        extra = 'allow'

class CustomVectorStoreConfig(BaseVectorStoreConfig):
    connection_string: str = Field(default='')
    collection_name: str = Field(default='memories')
    class Config:
        extra = 'allow'

from powermem.integrations.llm.factory import LLMFactory
from powermem.integrations.embeddings.factory import EmbedderFactory
from powermem.storage.factory import VectorStoreFactory

LLMFactory.provider_to_class.update({
    "custom": ("powermem.integrations.llm.custom.CustomLLM", CustomLLMConfig),
})

EmbedderFactory.provider_to_class.update({
    "custom": "powermem.integrations.embeddings.custom.CustomEmbedder"
})

VectorStoreFactory.provider_to_class.update({
     "custom": "powermem.storage.custom.custom_integration_example.CustomVectorStore"
})
```

## Step 1: Custom LLM Provider

Implement a custom LLM provider:

```python
# custom_integration_example.py
from powermem.integrations.llm.base import LLMBase
from typing import List, Dict, Any

class CustomLLM(LLMBase):
    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get('api_key', '')
        self.model = config.get('model', 'default')
        self.base_url = config.get('base_url', 'https://api.example.com')
    
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate text using custom LLM"""
        # Your custom LLM implementation
        # This is a mock example
        return f"Response to: {prompt}"
    
    def extract_facts(self, messages: List[Dict[str, str]]) -> List[str]:
        """Extract facts from messages"""
        # Custom fact extraction logic
        facts = []
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                # Simple extraction (replace with your logic)
                if 'name is' in content.lower():
                    facts.append(f"Name: {content.split('name is')[1].strip()}")
        return facts

# Register custom LLM
from powermem.integrations.llm.factory import LLMFactory

# Register your custom provider
LLMFactory.register('custom', CustomLLM)

# Use custom LLM
config = {
    'llm': {
        'provider': 'custom',
        'config': {
            'api_key': 'your_key',
            'model': 'your_model',
            'base_url': 'https://api.example.com'
        }
    },
    'embedder': {
        'provider': 'qwen',
        'config': {'api_key': 'your_key', 'model': 'text-embedding-v4'}
    },
    'vector_store': {
        'provider': 'sqlite',
        'config': {'path': './memories.db'}
    }
}

from powermem import Memory
memory = Memory(config=config)
```

## Step 2: Custom Embedding Provider

Implement a custom embedding provider:

```python
# custom_integration_example.py
from powermem.integrations.embeddings.base import EmbedderBase
from typing import List
import numpy as np

class CustomEmbedder(EmbedderBase):
    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get('api_key', '')
        self.model = config.get('model', 'default')
        self.dims = config.get('dims', 768)
    
    def embed(self, text: str) -> List[float]:
        """Generate embedding for text"""
        # Your custom embedding implementation
        # This is a mock example
        # In real implementation, call your embedding API
        return np.random.rand(self.dims).tolist()
    
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for batch of texts"""
        return [self.embed(text) for text in texts]

# Register custom embedder
from powermem.integrations.embeddings.factory import EmbedderFactory

EmbedderFactory.register('custom', CustomEmbedder)

# Use custom embedder
config = {
    'llm': {
        'provider': 'qwen',
        'config': {'api_key': 'your_key', 'model': 'qwen-plus'}
    },
    'embedder': {
        'provider': 'custom',
        'config': {
            'api_key': 'your_key',
            'model': 'your_model',
            'dims': 768
        }
    },
    'vector_store': {
        'provider': 'sqlite',
        'config': {'path': './memories.db'}
    }
}

memory = Memory(config=config)
```

## Step 3: Custom Storage Backend

Implement a custom storage backend:

```python
# custom_integration_example.py
from powermem.storage.base import VectorStoreBase
from typing import List, Dict, Any, Optional

class CustomVectorStore(VectorStoreBase):
    def __init__(self, config: Dict[str, Any]):
        self.connection_string = config.get('connection_string', '')
        self.collection_name = config.get('collection_name', 'memories')
        # Initialize your custom storage connection
        self._init_connection()
    
    def _init_connection(self):
        """Initialize connection to your storage"""
        # Your connection initialization logic
        pass
    
    def add(self, memory: str, embedding: List[float], metadata: Dict[str, Any]) -> str:
        """Add memory to storage"""
        memory_id = f"mem_{hash(memory)}"
        # Your storage implementation
        # Store memory, embedding, and metadata
        return memory_id
    
    def search(self, query_embedding: List[float], limit: int = 10, 
               filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Search similar memories"""
        # Your search implementation
        # Return list of memories with scores
        return []
    
    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get memory by ID"""
        # Your retrieval implementation
        return None
    
    def update(self, memory_id: str, memory: Optional[str] = None,
               metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Update memory"""
        # Your update implementation
        return True
    
    def delete(self, memory_id: str) -> bool:
        """Delete memory"""
        # Your deletion implementation
        return True
    
    def delete_all(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Delete all matching memories"""
        # Your bulk deletion implementation
        return 0

# Register custom storage
from powermem.storage.factory import VectorStoreFactory

VectorStoreFactory.register('custom', CustomVectorStore)

# Use custom storage
config = {
    'llm': {
        'provider': 'qwen',
        'config': {'api_key': 'your_key', 'model': 'qwen-plus'}
    },
    'embedder': {
        'provider': 'qwen',
        'config': {'api_key': 'your_key', 'model': 'text-embedding-v4'}
    },
    'vector_store': {
        'provider': 'custom',
        'config': {
            'connection_string': 'your_connection_string',
            'collection_name': 'memories'
        }
    }
}

memory = Memory(config=config)
```

## Step 4: LangChain Integration

Integrate powermem with LangChain:

```python
# langchain_integration.py
from langchain.memory import ConversationBufferMemory
from powermem import Memory, auto_config

class PowermemLangChainMemory(ConversationBufferMemory):
    def __init__(self, powermem_instance: Memory, user_id: str, **kwargs):
        super().__init__(**kwargs)
        self.powermem = powermem_instance
        self.user_id = user_id
    
    def save_context(self, inputs, outputs):
        """Save conversation to powermem"""
        super().save_context(inputs, outputs)
        
        # Convert to messages format
        messages = [
            {"role": "user", "content": str(inputs)},
            {"role": "assistant", "content": str(outputs)}
        ]
        
        # Save to powermem with intelligent processing
        self.powermem.add(
            messages=messages,
            user_id=self.user_id,
            infer=True
        )
    
    def load_memory_variables(self, inputs):
        """Load relevant memories from powermem"""
        query = str(inputs)
        
        # Search powermem for relevant memories
        results = self.powermem.search(
            query=query,
            user_id=self.user_id,
            limit=5
        )
        
        # Format for LangChain
        memory_variables = {
            "history": "\n".join([
                result['memory'] for result in results.get('results', [])
            ])
        }
        
        return memory_variables

# Usage with LangChain
from langchain.llms import OpenAI
from langchain.chains import ConversationChain

config = auto_config()
powermem = Memory(config=config)
memory = PowermemLangChainMemory(powermem, user_id="user123")

llm = OpenAI()
chain = ConversationChain(
    llm=llm,
    memory=memory
)

response = chain.run("Hello, I'm Alice")
print(response)
```

## Step 5: FastAPI Integration

Create a FastAPI application with powermem:

```python
# fastapi_integration.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from powermem import AsyncMemory, auto_config
import asyncio

app = FastAPI()
config = auto_config()
async_memory = None

@app.on_event("startup")
async def startup():
    global async_memory
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

class MemoryRequest(BaseModel):
    memory: str
    user_id: str
    metadata: dict = {}

class SearchRequest(BaseModel):
    query: str
    user_id: str
    limit: int = 10

@app.post("/memories")
async def add_memory(request: MemoryRequest):
    """Add a memory"""
    try:
        result = await async_memory.add(
            request.memory,  # messages as first positional argument
            user_id=request.user_id,
            metadata=request.metadata
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/memories/search")
async def search_memories(request: SearchRequest):
    """Search memories"""
    try:
        results = await async_memory.search(
            query=request.query,
            user_id=request.user_id,
            limit=request.limit
        )
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/memories/{user_id}")
async def get_all_memories(user_id: str):
    """Get all memories for a user"""
    try:
        results = await async_memory.get_all(user_id=user_id)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/memories/{user_id}")
async def delete_all_memories(user_id: str):
    """Delete all memories for a user"""
    try:
        result = await async_memory.delete_all(user_id=user_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Run with: uvicorn fastapi_integration:app --reload
```

## Step 6: Custom Intelligence Plugin

Implement a custom intelligence plugin:

```python
# custom_intelligence_plugin.py
from powermem.intelligence.plugin import IntelligentMemoryPlugin
from typing import Dict, Any
from datetime import datetime, timedelta

class CustomIntelligencePlugin(IntelligentMemoryPlugin):
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.decay_rate = config.get('decay_rate', 0.1)
    
    def calculate_retention(self, memory: Dict[str, Any], 
                           time_since_creation: float) -> float:
        """Custom retention calculation"""
        # Your custom retention logic
        initial_retention = 1.0
        retention = initial_retention * (0.9 ** (time_since_creation / 86400))
        return max(0.0, min(1.0, retention))
    
    def score_importance(self, memory: Dict[str, Any]) -> float:
        """Custom importance scoring"""
        # Your custom importance logic
        content = memory.get('memory', '')
        if 'important' in content.lower() or 'critical' in content.lower():
            return 0.9
        return 0.5

# Use custom plugin
config = {
    'intelligent_memory': {
        'enabled': True,
        'plugin': 'custom',
        'decay_rate': 0.1
    },
    # ... other config
}
```

## Complete Example

Here's a complete custom integration example:

```python
# complete_custom_integration.py
from powermem import Memory
from powermem.integrations.llm.base import LLMBase
from typing import List, Dict, Any

class SimpleLLM(LLMBase):
    def __init__(self, config):
        self.model = config.get('model', 'simple')
    
    def generate(self, prompt, **kwargs):
        return f"Response: {prompt}"
    
    def extract_facts(self, messages):
        facts = []
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                if content:
                    facts.append(content)
        return facts

# Register
from powermem.integrations.llm.factory import LLMFactory
LLMFactory.register('simple', SimpleLLM)

# Use
config = {
    'llm': {
        'provider': 'simple',
        'config': {'model': 'simple'}
    },
    'embedder': {
        'provider': 'qwen',
        'config': {'api_key': 'your_key', 'model': 'text-embedding-v4'}
    },
    'vector_store': {
        'provider': 'sqlite',
        'config': {'path': './memories.db'}
    }
}

memory = Memory(config=config)
result = memory.add("Test memory", user_id="user123")
print(f"✓ Added memory with custom LLM: {result}")
```

## Extension Exercises

### Exercise 1: Custom Storage Backend

Implement a file-based storage backend:

```python
class FileVectorStore(VectorStoreBase):
    def __init__(self, config):
        self.file_path = config.get('file_path', './memories.json')
        self.memories = self._load()
    
    def _load(self):
        import json
        import os
        if os.path.exists(self.file_path):
            with open(self.file_path, 'r') as f:
                return json.load(f)
        return {}
    
    def _save(self):
        import json
        with open(self.file_path, 'w') as f:
            json.dump(self.memories, f)
    
    def add(self, memory, embedding, metadata):
        memory_id = f"mem_{len(self.memories)}"
        self.memories[memory_id] = {
            'memory': memory,
            'embedding': embedding,
            'metadata': metadata
        }
        self._save()
        return memory_id
```

### Exercise 2: Custom Embedding with Caching

Add caching to custom embedder:

```python
class CachedEmbedder(EmbedderBase):
    def __init__(self, config):
        self.cache = {}
        self.base_embedder = YourEmbedder(config)
    
    def embed(self, text):
        if text in self.cache:
            return self.cache[text]
        embedding = self.base_embedder.embed(text)
        self.cache[text] = embedding
        return embedding
```

## Best Practices

1. **Follow interfaces**: Implement required methods from base classes
2. **Error handling**: Handle errors gracefully
3. **Configuration**: Use configuration dictionaries for flexibility
4. **Testing**: Test custom providers thoroughly
5. **Documentation**: Document custom implementations

