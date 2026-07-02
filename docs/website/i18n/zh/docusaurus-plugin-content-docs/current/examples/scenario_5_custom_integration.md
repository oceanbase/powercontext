# 场景 5：自定义集成 {#scenario-5-custom-integration}

本场景演示如何将 PowerMem 集成到自定义系统中，如何实现自定义提供者，以及如何扩展功能。

## 前置条件 {#prerequisites}

- 完成之前的场景
- 理解 Python 类和接口
- 基本了解 LLM/embedding 提供者

## 理解自定义集成 {#understanding-custom-integration}

PowerMem 旨在具有可扩展性：
- 自定义 LLM 提供者
- 自定义 embedding 提供者
- 自定义存储后端
- 自定义智能插件

## 提供者和工厂配置 {#provider-and-factory-configuration}

使用配置类进行工厂配置：
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
## 第一步：自定义 LLM 提供者 {#step-1-custom-llm-provider}

实现一个自定义的 LLM 提供者：
```python
# custom_integration_example.py
from powermem.integrations.llm.base import LLMBase
from typing import List, Dict, Any

class CustomLLM(LLMBase):
    def __init__(self, config: Dict[str, Any]):
        self.credential = config.get('api_key', '')
        self.model = config.get('model', 'default')
        self.base_url = config.get('base_url', 'https://api.example.com')

    def generate(self, prompt: str, **kwargs) -> str:
        """使用自定义 LLM 生成文本"""
        # 您的自定义 LLM 实现
        # 这是一个模拟示例
        return f"Response to: {prompt}"

    def extract_facts(self, messages: List[Dict[str, str]]) -> List[str]:
        """从消息中提取事实"""
        # 自定义事实提取逻辑
        facts = []
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                # 简单提取（请替换为您的逻辑）
                if 'name is' in content.lower():
                    facts.append(f"Name: {content.split('name is')[1].strip()}")
        return facts

# 注册自定义 LLM
from powermem.integrations.llm.factory import LLMFactory

# 注册您的自定义 provider
LLMFactory.register('custom', CustomLLM)

# 使用自定义 LLM
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
**重要提示：**
- 如果您提供了一个 `config` 参数，PowerMem 将会**忽略**任何 `.env` 文件中的设置
- 这使您可以通过编程方式配置 PowerMem，而无需依赖环境变量

## 第2步：自定义 Embedding 提供者 {#step-2-custom-embedding-provider}

实现一个自定义的 embedding 提供者：
```python
# custom_integration_example.py
from powermem.integrations.embeddings.base import EmbedderBase
from typing import List
import numpy as np

class CustomEmbedder(EmbedderBase):
    def __init__(self, config: Dict[str, Any]):
        self.credential = config.get('api_key', '')
        self.model = config.get('model', 'default')
        self.dims = config.get('dims', 768)

    def embed(self, text: str) -> List[float]:
        """为文本生成 Embedding"""
        # 您的自定义 Embedding 实现
        # 这是一个模拟示例
        # 实际实现中调用您的 Embedding API
        return np.random.rand(self.dims).tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """为一批文本生成 Embedding"""
        return [self.embed(text) for text in texts]

# 注册自定义 Embedder
from powermem.integrations.embeddings.factory import EmbedderFactory

EmbedderFactory.register('custom', CustomEmbedder)

# 使用自定义 Embedder
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
## 第 3 步：自定义存储后端 {#step-3-custom-storage-backend}

实现一个自定义存储后端：
```python
# custom_integration_example.py
from powermem.storage.base import VectorStoreBase
from typing import List, Dict, Any, Optional

class CustomVectorStore(VectorStoreBase):
    def __init__(self, config: Dict[str, Any]):
        self.connection_string = config.get('connection_string', '')
        self.collection_name = config.get('collection_name', 'memories')
        # 初始化您的自定义存储连接
        self._init_connection()

    def _init_connection(self):
        """初始化到您存储的连接"""
        # 您的连接初始化逻辑
        pass

    def add(self, memory: str, embedding: List[float], metadata: Dict[str, Any]) -> str:
        """向存储添加记忆"""
        memory_id = f"mem_{hash(memory)}"
        # 您的存储实现
        # 存储记忆、Embedding 和 metadata
        return memory_id

    def search(self, query_embedding: List[float], limit: int = 10,
               filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """搜索相似记忆"""
        # 您的搜索实现
        # 返回带分数的记忆列表
        return []

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 获取记忆"""
        # 您的检索实现
        return None

    def update(self, memory_id: str, memory: Optional[str] = None,
               metadata: Optional[Dict[str, Any]] = None) -> bool:
        """更新记忆"""
        # 您的更新实现
        return True

    def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        # 您的删除实现
        return True

    def delete_all(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """删除所有匹配的记忆"""
        # 您的批量删除实现
        return 0

# 注册自定义存储
from powermem.storage.factory import VectorStoreFactory

VectorStoreFactory.register('custom', CustomVectorStore)

# 使用自定义存储
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
## 第4步：LangChain 集成 {#step-4-langchain-integration}

使用新的 Runnable API，将 PowerMem 集成到 LangChain 1.1.0+ 中：
```python
# langchain_integration.py
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from powermem import Memory, auto_config
from typing import List, Dict, Any

# 创建 PowerMem 实例
config = auto_config()
powermem = Memory(config=config)

class PowermemLangChainMemory:
    """用于 LangChain 1.1.0+ 集成的自定义记忆类。"""

    def __init__(self, powermem_instance, user_id: str):
        self.powermem = powermem_instance
        self.user_id = user_id
        self.messages: List[BaseMessage] = []

    def add_message(self, message: BaseMessage):
        """向对话历史添加消息。"""
        self.messages.append(message)

    def get_messages(self) -> List[BaseMessage]:
        """获取所有对话消息。"""
        return self.messages

    def save_to_powermem(self, user_input: str, assistant_output: str):
        """使用智能处理将对话保存到 PowerMem。"""
        messages = [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": assistant_output}
        ]
        self.powermem.add(
            messages=messages,
            user_id=self.user_id,
            infer=True  # 启用智能事实提取
        )

    def get_context(self, query: str) -> str:
        """从 PowerMem 加载相关记忆。"""
        results = self.powermem.search(
            query=query,
            user_id=self.user_id,
            limit=5
        )
        memories = results.get('results', [])
        if memories:
            return "\n".join([mem.get('memory', '') for mem in memories])
        return "No previous context found."

# 与 LangChain 1.1.0+ 一起使用
llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0.7)
memory = PowermemLangChainMemory(powermem, user_id="user123")

# 创建 Prompt 模板
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant. Use the following context to provide personalized responses."),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}")
])

# 构建带上下文检索的链
def format_messages(input_dict: Dict[str, Any]) -> Dict[str, Any]:
    """检索上下文并格式化消息。"""
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

# 使用 LCEL（LangChain Expression Language）创建链
chain = (
    RunnableLambda(format_messages)
    | prompt
    | llm
)

# 使用链
user_input = "Hello, I'm Alice"
memory.add_message(HumanMessage(content=user_input))

response = chain.invoke({"input": user_input})
response_text = response.content if hasattr(response, 'content') else str(response)

memory.add_message(AIMessage(content=response_text))
memory.save_to_powermem(user_input, response_text)

print(response_text)
```
### LangGraph 集成 {#langgraph-integration}

将 PowerMem 与 LangGraph 1.0+ 集成，用于有状态的工作流：
```python
# langgraph_integration.py
from langgraph.graph import StateGraph, END, START
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from powermem import Memory, auto_config
from typing import TypedDict, Annotated, List

# 创建 PowerMem 实例
config = auto_config()
powermem = Memory(config=config)

# 定义状态 schema
class ConversationState(TypedDict):
    messages: Annotated[List[BaseMessage], "Conversation messages"]
    user_id: str
    context: dict

# 初始化 LLM
llm = ChatOpenAI(model="gpt-3.5-turbo")

# 定义节点
def load_context(state: ConversationState) -> ConversationState:
    """从 PowerMem 加载相关上下文。"""
    user_id = state["user_id"]
    last_message = state["messages"][-1] if state["messages"] else None
    query = last_message.content if last_message else ""

    # 搜索 powermem
    results = powermem.search(query=query, user_id=user_id, limit=5)
    state["context"] = {
        "memories": [mem.get('memory', '') for mem in results.get('results', [])]
    }
    return state

def generate_response(state: ConversationState) -> ConversationState:
    """使用 LLM 生成响应。"""
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
    """将对话保存到 PowerMem。"""
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

# 构建图
workflow = StateGraph(ConversationState)
workflow.add_node("load_context", load_context)
workflow.add_node("generate_response", generate_response)
workflow.add_node("save_conversation", save_conversation)

workflow.add_edge(START, "load_context")
workflow.add_edge("load_context", "generate_response")
workflow.add_edge("generate_response", "save_conversation")
workflow.add_edge("save_conversation", END)

app = workflow.compile()

# 使用图
initial_state = {
    "messages": [HumanMessage(content="Hello, I'm Alice")],
    "user_id": "user123",
    "context": {}
}

final_state = app.invoke(initial_state)
print(final_state["messages"][-1].content)
```
## 第五步：FastAPI 集成 {#step-5-fastapi-integration}

使用 powermem 创建一个 FastAPI 应用程序：
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
    """添加记忆"""
    try:
        result = await async_memory.add(
            request.memory,  # messages 作为第一个位置参数
            user_id=request.user_id,
            metadata=request.metadata
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/memories/search")
async def search_memories(request: SearchRequest):
    """搜索记忆"""
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
    """获取用户的所有记忆"""
    try:
        results = await async_memory.get_all(user_id=user_id)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/memories/{user_id}")
async def delete_all_memories(user_id: str):
    """删除用户的所有记忆"""
    try:
        result = await async_memory.delete_all(user_id=user_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 运行方式：uvicorn fastapi_integration:app --reload
```
## 第六步：自定义智能插件 {#step-6-custom-intelligence-plugin}

实现一个自定义智能插件：
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
        """自定义保持率计算"""
        # 您的自定义保持率逻辑
        initial_retention = 1.0
        retention = initial_retention * (0.9 ** (time_since_creation / 86400))
        return max(0.0, min(1.0, retention))

    def score_importance(self, memory: Dict[str, Any]) -> float:
        """自定义重要性评分"""
        # 您的自定义重要性逻辑
        content = memory.get('memory', '')
        if 'important' in content.lower() or 'critical' in content.lower():
            return 0.9
        return 0.5

# 使用自定义插件
config = {
    'intelligent_memory': {
        'enabled': True,
        'plugin': 'custom',
        'decay_rate': 0.1
    },
    # ... 其他配置
}
```
## 完整示例 {#complete-example}

以下是一个完整的自定义集成示例：
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

# 注册
from powermem.integrations.llm.factory import LLMFactory
LLMFactory.register('simple', SimpleLLM)

# 使用
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
## 拓展练习 {#extension-exercises}

### 练习 1：自定义存储后端 {#exercise-1-custom-storage-backend}

实现一个基于文件的存储后端：
```python
class FileVectorStore(VectorStoreBase):
    def __init__(self, config):
        self.file_path = config.get('file_path', './memories.json')
        self.memories = self._load()

    def _load(self):
        import json
from json import dump, dumps
        import os
        if os.path.exists(self.file_path):
            with open(self.file_path, 'r') as f:
                return json.load(f)
        return {}

    def _save(self):
        import json
from json import dump, dumps
        with open(self.file_path, 'w') as f:
            dump(self.memories, f)

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
### 练习 2：带缓存的自定义 Embedding {#exercise-2-custom-embedding-with-caching}

为自定义 embedder 添加缓存：
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
## 最佳实践 {#best-practices}

1. **遵循接口**：实现基类中的必要方法
2. **错误处理**：优雅地处理错误
3. **配置**：使用配置字典以提高灵活性
4. **测试**：彻底测试自定义提供者
5. **文档**：记录自定义实现
