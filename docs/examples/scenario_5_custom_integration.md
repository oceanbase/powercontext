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

**Important Notes:**
- If you provide a `config` parameter, PowerMem will **ignore** any `.env` file settings
- This allows you to programmatically configure PowerMem without relying on environment variables

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

Integrate powermem with LangChain 1.1.0+ using the new Runnable API:

```python
# langchain_integration.py
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from powermem import Memory, auto_config
from typing import List, Dict, Any

# Create powermem instance
config = auto_config()
powermem = Memory(config=config)

class PowermemLangChainMemory:
    """Custom memory class for LangChain 1.1.0+ integration."""
    
    def __init__(self, powermem_instance, user_id: str):
        self.powermem = powermem_instance
        self.user_id = user_id
        self.messages: List[BaseMessage] = []
    
    def add_message(self, message: BaseMessage):
        """Add a message to conversation history."""
        self.messages.append(message)
    
    def get_messages(self) -> List[BaseMessage]:
        """Get all conversation messages."""
        return self.messages
    
    def save_to_powermem(self, user_input: str, assistant_output: str):
        """Save conversation to powermem with intelligent processing."""
        messages = [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": assistant_output}
        ]
        self.powermem.add(
            messages=messages,
            user_id=self.user_id,
            infer=True  # Enable intelligent fact extraction
        )
    
    def get_context(self, query: str) -> str:
        """Load relevant memories from powermem."""
        results = self.powermem.search(
            query=query,
            user_id=self.user_id,
            limit=5
        )
        memories = results.get('results', [])
        if memories:
            return "\n".join([mem.get('memory', '') for mem in memories])
        return "No previous context found."

# Usage with LangChain 1.1.0+
llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0.7)
memory = PowermemLangChainMemory(powermem, user_id="user123")

# Create prompt template
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant. Use the following context to provide personalized responses."),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}")
])

# Build the chain with context retrieval
def format_messages(input_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Retrieve context and format messages."""
    user_input = input_dict.get("input", "")
    context = memory.get_context(user_input)
    messages = memory.get_messages()
    
    formatted_history = []
    if context and "No previous" not in context:
        formatted_history.append(("system", f"Context: {context}"))
    for msg in messages:
        if isinstance(msg, HumanMessage):
            formatted_history.append(("human", msg.content))
        elif isinstance(msg, AIMessage):
            formatted_history.append(("assistant", msg.content))
    
    return {"history": formatted_history, "input": user_input}

# Create the chain using LCEL (LangChain Expression Language)
chain = (
    RunnableLambda(format_messages)
    | prompt
    | llm
)

# Use the chain
user_input = "Hello, I'm Alice"
memory.add_message(HumanMessage(content=user_input))

response = chain.invoke({"input": user_input})
response_text = response.content if hasattr(response, 'content') else str(response)

memory.add_message(AIMessage(content=response_text))
memory.save_to_powermem(user_input, response_text)

print(response_text)
```

### LangGraph Integration

Integrate powermem with LangGraph 1.0+ for stateful workflows:

```python
# langgraph_integration.py
from langgraph.graph import StateGraph, END, START
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from powermem import Memory, auto_config
from typing import TypedDict, Annotated, List

# Create powermem instance
config = auto_config()
powermem = Memory(config=config)

# Define state schema
class ConversationState(TypedDict):
    messages: Annotated[List[BaseMessage], "Conversation messages"]
    user_id: str
    context: dict

# Initialize LLM
llm = ChatOpenAI(model="gpt-3.5-turbo")

# Define nodes
def load_context(state: ConversationState) -> ConversationState:
    """Load relevant context from powermem."""
    user_id = state["user_id"]
    last_message = state["messages"][-1] if state["messages"] else None
    query = last_message.content if last_message else ""
    
    # Search powermem
    results = powermem.search(query=query, user_id=user_id, limit=5)
    state["context"] = {
        "memories": [mem.get('memory', '') for mem in results.get('results', [])]
    }
    return state

def generate_response(state: ConversationState) -> ConversationState:
    """Generate response using LLM."""
    last_message = state["messages"][-1]
    context_str = "\n".join(state["context"].get("memories", []))
    
    prompt = f"""Context from previous conversations:
{context_str}

User: {last_message.content}
Assistant:"""
    
    response = llm.invoke(prompt)
    state["messages"].append(AIMessage(content=response.content))
    return state

def save_conversation(state: ConversationState) -> ConversationState:
    """Save conversation to powermem."""
    messages = state["messages"]
    if len(messages) >= 2:
        user_msg = messages[-2]
        ai_msg = messages[-1]
        
        powermem.add(
            messages=[
                {"role": "user", "content": user_msg.content},
                {"role": "assistant", "content": ai_msg.content}
            ],
            user_id=state["user_id"],
            infer=True
        )
    return state

# Build the graph
workflow = StateGraph(ConversationState)
workflow.add_node("load_context", load_context)
workflow.add_node("generate_response", generate_response)
workflow.add_node("save_conversation", save_conversation)

workflow.add_edge(START, "load_context")
workflow.add_edge("load_context", "generate_response")
workflow.add_edge("generate_response", "save_conversation")
workflow.add_edge("save_conversation", END)

app = workflow.compile()

# Use the graph
initial_state = {
    "messages": [HumanMessage(content="Hello, I'm Alice")],
    "user_id": "user123",
    "context": {}
}

final_state = app.invoke(initial_state)
print(final_state["messages"][-1].content)
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

