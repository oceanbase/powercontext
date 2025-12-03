# Integrations Guide

Guide to integrating powermem with various frameworks and services.

## LangChain Integration

PowerMem integrates seamlessly with LangChain 1.1.0+ using the new Runnable API and LangChain Expression Language (LCEL).

### Basic Integration

```python
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from powermem import Memory, auto_config
from typing import List, Dict, Any

# Create powermem instance
config = auto_config()
powermem = Memory(config=config)

# Custom memory class for PowerMem integration
class PowermemMemory:
    """Custom memory class that integrates PowerMem with LangChain 1.1.0+."""
    
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
        """Save conversation to PowerMem with intelligent fact extraction."""
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
        """Retrieve relevant context from PowerMem."""
        results = self.powermem.search(
            query=query,
            user_id=self.user_id,
            limit=5
        )
        memories = results.get('results', [])
        if memories:
            return "\n".join([mem.get('memory', '') for mem in memories])
        return "No previous context found."
```

### Usage with LangChain 1.1.0+

```python
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI

# Initialize components
llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0.7)
memory = PowermemMemory(powermem, user_id="user123")

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

# Create the chain using LCEL
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
```

## LangGraph Integration

PowerMem integrates with LangGraph 1.0+ for stateful conversation workflows.

### Basic Integration

```python
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
    """Load relevant context from PowerMem."""
    user_id = state["user_id"]
    last_message = state["messages"][-1] if state["messages"] else None
    query = last_message.content if last_message else ""
    
    # Search PowerMem
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
    """Save conversation to PowerMem."""
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
```

## FastAPI Integration

### Creating API Endpoints

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from powermem import Memory, auto_config

app = FastAPI()
config = auto_config()
memory = Memory(config=config)

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
    try:
        result = memory.add(
            messages=request.memory,
            user_id=request.user_id,
            metadata=request.metadata
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/memories/search")
async def search_memories(request: SearchRequest):
    try:
        results = memory.search(
            query=request.query,
            user_id=request.user_id,
            limit=request.limit
        )
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

## Async Integration

### Using with Async Frameworks

```python
from powermem import AsyncMemory, auto_config
import asyncio

async def async_example():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    
    # Add memory
    await async_memory.add(
        messages="User preference",
        user_id="user123"
    )
    
    # Search
    results = await async_memory.search(
        query="preferences",
        user_id="user123"
    )
    
    return results

# Run async
results = asyncio.run(async_example())
```

## Custom LLM Integration

### Implementing Custom LLM Provider

```python
from powermem.integrations.llm.base import LLMBase

class CustomLLM(LLMBase):
    def __init__(self, config):
        self.api_key = config.get('api_key')
        self.model = config.get('model', 'default')
    
    def generate(self, prompt, **kwargs):
        # Your custom LLM implementation
        response = your_llm_client.generate(
            prompt=prompt,
            model=self.model,
            api_key=self.api_key
        )
        return response
    
    def extract_facts(self, messages):
        # Custom fact extraction logic
        facts = your_custom_extractor(messages)
        return facts
```

## Custom Embedding Integration

### Implementing Custom Embedder

```python
from powermem.integrations.embeddings.base import EmbedderBase

class CustomEmbedder(EmbedderBase):
    def __init__(self, config):
        self.api_key = config.get('api_key')
        self.model = config.get('model', 'default')
    
    def embed(self, text):
        # Your custom embedding implementation
        embedding = your_embedding_client.embed(
            text=text,
            model=self.model,
            api_key=self.api_key
        )
        return embedding
    
    def embed_batch(self, texts):
        # Batch embedding
        embeddings = []
        for text in texts:
            embeddings.append(self.embed(text))
        return embeddings
```

## Custom Storage Integration

### Implementing Custom Vector Store

```python
from powermem.storage.base import VectorStoreBase

class CustomVectorStore(VectorStoreBase):
    def __init__(self, config):
        self.connection = your_database_connect(config)
    
    def add(self, memory, embedding, metadata):
        # Your custom storage implementation
        self.connection.insert({
            'memory': memory,
            'embedding': embedding,
            'metadata': metadata
        })
    
    def search(self, query_embedding, limit, filters):
        # Your custom search implementation
        results = self.connection.similarity_search(
            query_embedding=query_embedding,
            limit=limit,
            filters=filters
        )
        return results
```

## OpenAI Integration

### Using OpenAI Models

```env
LLM_PROVIDER=openai
LLM_API_KEY=your_openai_api_key
LLM_MODEL=gpt-4
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=your_openai_api_key
EMBEDDING_MODEL=text-embedding-3-large
```

```python
from powermem import create_memory

memory = create_memory()  # Auto-loads OpenAI config from .env
```

## Anthropic Integration

### Using Claude Models

```env
LLM_PROVIDER=anthropic
LLM_API_KEY=your_anthropic_api_key
LLM_MODEL=claude-3-opus-20240229
```

```python
from powermem import create_memory

memory = create_memory()  # Auto-loads Anthropic config
```

## Qwen Integration

### Using Qwen Models

```env
LLM_PROVIDER=qwen
LLM_API_KEY=your_dashscope_api_key
LLM_MODEL=qwen-plus
EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=your_dashscope_api_key
EMBEDDING_MODEL=text-embedding-v4
```

```python
from powermem import create_memory

memory = create_memory()  # Auto-loads Qwen config
```

## Best Practices

1. **Use environment variables**: Keep API keys in .env files
2. **Error handling**: Always handle exceptions in integrations
3. **Async for scale**: Use AsyncMemory for high-throughput scenarios
4. **Custom providers**: Implement custom providers when needed
5. **Testing**: Test integrations thoroughly

## See Also

- [Getting Started Guide](docs/guides/0001-getting_started.md)
- [Configuration Guide](docs/guides/0003-configuration.md)

