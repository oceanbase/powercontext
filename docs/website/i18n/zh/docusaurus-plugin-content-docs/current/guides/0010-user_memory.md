---
title: UserMemory 使用指南
sidebar_label: UserMemory 使用指南
---

# UserMemory 使用指南 {#usermemory-guide}

UserMemory 是 PowerMem 的高级用户档案管理模块。它能够自动从对话中提取并维护用户档案信息，同时管理对话事件记忆。

## 前置条件 {#prerequisites}

- Python 3.11+
- 已安装 PowerMem (`pip install powermem`)
- 已配置 LLM 和 embedding 服务（用于档案提取）
- 已配置 vector store（用于存储记忆和档案）

## 概述 {#overview}

UserMemory 在 `Memory` 的基础上增加了用户档案管理功能：

1. **自动档案提取**：从对话中提取与用户相关的信息（基本信息、兴趣、工作背景等）
2. **持续档案更新**：根据新的对话更新和完善档案
3. **记忆与档案结合**：在搜索记忆时可选地包含档案信息

### 核心功能 {#core-capabilities}

- **对话存储**：将用户对话存储为事件记忆
- **档案提取**：使用 LLM 从对话中提取用户档案信息
- **档案管理**：保存、更新和查询用户档案
- **联合搜索**：在记忆搜索结果中可选地包含档案信息

## 初始化 {#initialization}

### 基本初始化 {#basic-initialization}

`UserMemory` 的初始化与 `Memory` 类似，并接受相同的配置选项：
```python
from powermem import UserMemory, auto_config

# 使用自动配置（从 .env 加载）
config = auto_config()
user_memory = UserMemory(config=config)
```
### 使用字典配置 {#configure-with-dict}
```python
from powermem import UserMemory

config = {
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'qwen-plus',
        }
    },
    'embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'text-embedding-v4'
        }
    },
    'vector_store': {
        'provider': 'oceanbase',
        'config': {
            'collection_name': 'memories',
            'connection_args': {
                'host': 'localhost',
                'port': 2881,
                'user': 'root@sys',
                'password': 'password',
                'db_name': 'powermem'
            }
        }
    }
}

user_memory = UserMemory(config=config)
```
> **重要提示**: `UserMemory` 内部会创建一个 `Memory` 实例用于存储会话事件，并创建一个 `UserProfileStore` 用于存储用户档案。目前，`UserProfileStore` 仅支持 OceanBase 作为存储后端。如果尝试使用其他存储提供者（例如 SQLite、PostgreSQL）与 `UserMemory` 一起使用，将会引发一个带有明确错误信息的 `ValueError`。要使用 `UserMemory`，请将 OceanBase 配置为您的向量存储提供者。

## 核心 API {#core-apis}

### 1. add() — 添加会话并提取用户档案 {#1-add--add-conversation-and-extract-user-profile}

添加会话内容，自动将其存储为事件记忆，并提取/更新用户档案。

#### 签名 {#signature}
```python
def add(
    self,
    messages,
    user_id: str,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    filters: Optional[Dict[str, Any]] = None,
    scope: Optional[str] = None,
    memory_type: Optional[str] = None,
    prompt: Optional[str] = None,
    infer: bool = True,
    profile_type: str = "content",
    custom_topics: Optional[str] = None,
    strict_mode: bool = False,
    include_roles: Optional[List[str]] = ["user"],
    exclude_roles: Optional[List[str]] = ["assistant"],
    native_language: Optional[str] = None,
) -> Dict[str, Any]
```
#### 参数 {#parameters}
与 `Memory.add()` 相同
- `messages` (str | dict | list[dict]): 对话内容，支持以下格式：
  - 字符串：原始对话字符串
  - 字典：包含 `role` 和 `content` 的单条消息
  - 列表：OpenAI 风格的消息 `[{"role": "user", "content": "..."}, ...]`
- `user_id` (str, 必填): 用户标识符
- `agent_id` (str, 可选): Agent 标识符
- `run_id` (str, 可选): 运行/会话标识符
- `metadata` (dict, 可选): 额外的元数据
- `filters` (dict, 可选): 高级过滤元数据
- `scope` (str, 可选): 记忆范围（例如，`user`、`agent`、`session`）
- `memory_type` (str, 可选): 记忆类型/类别
- `prompt` (str, 可选): 用于智能处理的自定义 prompt
- `infer` (bool): 是否启用智能记忆处理（默认值：True）
- `profile_type` (str): 配置文件提取的类型，可以是 "content"（非结构化）或 "topics"（结构化）。默认值："content"
- `custom_topics` (str, 可选): 用于结构化提取的自定义主题 JSON 字符串。仅在 profile_type="topics" 时使用。格式应为 JSON 字符串：
  ```json
  {
      "main_topic": {
          "sub_topic1": "description1",
          "sub_topic2": "description2"
      }
  }
  ```
- 所有键必须使用 snake_case 格式（小写、下划线、无空格）
- 描述仅供参考，**不应**在输出中用作键

- `strict_mode` (bool): 如果为 True，则仅输出提供列表中的主题。仅在 `profile_type="topics"` 时使用。默认值：False
- `include_roles` (List[str], optional): 在提取配置文件时，用于过滤消息的角色列表。默认值：`["user"]`。如果显式设置为 `None` 或 `[]`，则不应用包含过滤器。
- `exclude_roles` (List[str], optional): 在提取配置文件时，用于过滤消息的排除角色列表。默认值：`["assistant"]`。如果显式设置为 `None` 或 `[]`，则不应用排除过滤器。
- `native_language` (str, optional): ISO 639-1 语言代码（例如 "zh", "en", "ja"），用于指定配置文件提取的目标语言。如果指定，提取的配置文件将以该语言书写，而不论对话中使用的语言。如果未指定，配置文件语言将遵循对话语言。默认值：None

#### 返回值 {#return-value}

返回一个包含以下内容的字典：
```python
{
    # 来自 Memory.add()
    "results": [
        {
            "id": 123,                    # 记忆 ID
            "memory": "...",              # 记忆内容
            "event": "ADD",               # 事件类型（ADD/UPDATE/DELETE）
            "user_id": "user_001",        # 用户 ID
            "agent_id": "test_agent",     # Agent ID
            "run_id": "run_001",          # run ID
            "metadata": {...},              # 元数据
            "created_at": "2024-01-01T00:00:00",  # 创建时间
            "previous_memory": "..."      # 上一条记忆（仅 UPDATE）
        },
        ...
    ],
    "relations": {...},                   # 图关系（如果启用图存储）

    # UserMemory 新增字段
    "profile_extracted": True,            # 是否成功提取档案
    "profile_content": "..."             # 提取的档案内容（profile_type="content" 时）
    "topics": {...}                      # 结构化 topic 字典（profile_type="topics" 时）
}
```
#### 示例 {#examples}
```python
# 示例 1：添加对话列表
conversation = [
    {"role": "user", "content": "My name is Zhang. I'm a software engineer and I like Python."},
    {"role": "assistant", "content": "Nice to meet you, Zhang! Python is a great language."}
]

result = user_memory.add(
    messages=conversation,
    user_id="user_001",
    agent_id="test_agent",
    run_id="run_001"
)

print(f"Profile extracted: {result.get('profile_extracted', False)}")
if result.get('profile_content'):
    print(f"User profile: {result['profile_content']}")
print(f"Memory count: {len(result.get('results', []))}")

# 示例 2：添加单条消息
result = user_memory.add(
    messages={"role": "user", "content": "I also enjoy reading sci‑fi and watching movies."},
    user_id="user_001",
    agent_id="test_agent"
)

# 示例 3：添加原始字符串
result = user_memory.add(
    messages="The user mentioned they have been learning machine learning recently.",
    user_id="user_001"
)

# 示例 4：提取结构化 topic
custom_topics = '''
{
    "basic_information": {
        "user_name": "User's name",
        "age": "User's age",
        "location": "User's location"
    },
    "professional": {
        "occupation": "User's job title",
        "company": "User's company"
    },
    "interests": {
        "hobbies": "User's hobbies",
        "favorite_topics": "Topics user is interested in"
    }
}
'''

result = user_memory.add(
    messages=conversation,
    user_id="user_001",
    profile_type="topics",
    custom_topics=custom_topics,
    strict_mode=False
)

if result.get('topics'):
    print(f"Extracted topics: {result['topics']}")

# 示例 5：按角色过滤消息
# 默认仅使用 user 消息提取档案（排除 assistant 消息）
# 您可以自定义此行为：

# 包含所有角色（不过滤）
result = user_memory.add(
    messages=conversation,
    user_id="user_001",
    include_roles=None,  # 或 []
    exclude_roles=None   # 或 []
)

# 仅包含 user 和 system 消息，排除 tool 消息
result = user_memory.add(
    messages=conversation,
    user_id="user_001",
    include_roles=["user", "system"],
    exclude_roles=["tool"]
)

# 示例 6：指定档案提取的母语
# 用户说英语，但希望档案使用中文
result = user_memory.add(
    messages="I am a software engineer working in Beijing. I love drinking tea.",
    user_id="user_002",
    native_language="zh"  # 提取中文档案
)

if result.get('profile_content'):
    print(f"Profile (in Chinese): {result['profile_content']}")
    # 输出："用户是一名在北京工作的软件工程师。喜欢喝茶。"

# 使用母语提取结构化 topic
result = user_memory.add(
    messages="I'm 28 years old, working at Google in California.",
    user_id="user_003",
    profile_type="topics",
    native_language="zh"  # topic 值使用中文，key 保持英文
)

if result.get('topics'):
    print(f"Topics: {result['topics']}")
    # 输出：{"basic_information": {"user_age": "28"}, "employment": {"company": "谷歌", "location": "加利福尼亚"}}
```
### 2. search() — 搜索记忆（可选包含用户档案） {#2-search--search-memories-optionally-include-profile}

搜索相关记忆，并可选择在结果中包含用户的档案信息。

#### 签名 {#signature-1}
```python
def search(
    self,
    query: str,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    limit: int = 30,
    threshold: Optional[float] = None,
    add_profile: bool = False,
) -> Dict[str, Any]
```
#### 参数 {#parameters-1}
与 `Memory.search()` 相同
- `query` (str, 必填): 搜索查询字符串
- `user_id` (str, 可选): 按用户 ID 过滤
- `agent_id` (str, 可选): 按 Agent ID 过滤
- `run_id` (str, 可选): 按运行 ID 过滤
- `filters` (dict, 可选): 用于高级过滤的元数据过滤器
- `limit` (int): 最大结果数量（默认值: 30）
- `threshold` (float, 可选): 相似度阈值 (0.0–1.0) 用于过滤结果
- `add_profile` (bool): 是否在结果中包含用户档案（默认值: False）

#### 返回值 {#return-value-1}

返回一个包含以下内容的字典：
```python
{
    "results": [
        {
            "memory": "...",              # 记忆内容
            "metadata": {...},              # 元数据
            "score": 0.85,                  # 相似度分数
            "id": 123,                      # 记忆 ID
            "created_at": "2024-01-01T00:00:00",  # 创建时间
            "updated_at": "2024-01-01T00:00:00",  # 更新时间
            "user_id": "user_001",         # 用户 ID
            "agent_id": "test_agent",      # Agent ID
            "run_id": "run_001"            # run ID
        },
        ...
    ],
    "relations": [...],                     # 图关系（如果启用）

    # 如果 add_profile=True 且提供了 user_id
    "profile_content": "..."               # 用户档案内容（如果可用）
    "topics": {...}                        # 结构化 topic 字典（如果可用）
}
```
#### 示例 {#examples-1}
```python
# 示例 1：基础搜索
results = user_memory.search(
    query="user's work and interests",
    user_id="user_001",
    limit=10
)

for result in results.get('results', []):
    print(f"Memory: {result['memory']}")
    print(f"Score: {result.get('score', 0)}")
    print("---")

# 示例 2：搜索并包含用户档案
results = user_memory.search(
    query="user preferences",
    user_id="user_001",
    agent_id="test_agent",
    limit=5,
    threshold=0.7,  # 仅返回相似度 >= 0.7 的结果
    add_profile=True  # 包含用户档案
)

# 检查是否返回用户档案
if 'profile_content' in results:
    print(f"User profile: {results['profile_content']}")

# 遍历结果
for result in results.get('results', []):
    print(f"Memory: {result['memory']}")
    print(f"Score: {result.get('score', 0)}")
```
#### 可选：基于用户画像的查询重写 {#optional-query-rewrite-with-user-profile}

`UserMemory.search()` 可以根据用户画像可选地重写输入查询，以提高召回率。此功能默认关闭，需要在配置中设置 `query_rewrite.enabled=True` 才能启用。启用后：

- 仅当提供了 `user_id` 且用户存在 `profile_content` 时，重写才会运行
- 如果缺少画像、查询过短或重写失败，则会回退到原始查询
- 搜索 API 和返回结构不会发生变化

示例配置：
```python
config = {
    # ... 其他配置
    "query_rewrite": {
        "enabled": True,
        # 可选：重写 prompt 的自定义说明
        # "prompt": "根据用户档案将查询重写得更具体、更有依据。"
    }
}
```
您也可以通过环境变量启用此功能（参见 `.env.example`）：
```bash
QUERY_REWRITE_ENABLED=false
# QUERY_REWRITE_PROMPT=
# QUERY_REWRITE_MODEL_OVERRIDE=
```
- `QUERY_REWRITE_PROMPT` 是可选的自定义重写指令。
- `QUERY_REWRITE_MODEL_OVERRIDE` 是可选的，必须是同一 LLM 提供商系列中的另一个模型。

### 3. profile() — 获取用户档案 {#3-profile--get-user-profile}

直接获取特定用户的档案信息。

#### 签名 {#signature-2}
```python
def profile(
    self,
    user_id: str,
) -> Dict[str, Any]
```
#### 参数 {#parameters-2}

- `user_id` (str, 必填): 用户标识符

#### 返回值 {#return-value-2}

如果找到用户档案，返回一个包含以下内容的字典：
```python
{
    "id": 1,                               # 档案 ID
    "user_id": "user_001",                 # 用户 ID
    "profile_content": "...",              # 档案内容（文本，可用时）
    "topics": {...},                       # 结构化 topic 字典（如果可用）
    "created_at": "2024-01-01T00:00:00",   # 创建时间（ISO 格式）
    "updated_at": "2024-01-01T00:00:00"    # 最后更新时间（ISO 格式）
}
```
如果未找到配置文件，则返回一个空字典 `{}`。

#### 示例 {#examples-2}
```python
# 获取用户档案
profile = user_memory.profile(
    user_id="user_001"
)

if profile:
    print(f"User ID: {profile['user_id']}")
    if profile.get('profile_content'):
        print(f"Profile content: {profile['profile_content']}")
    if profile.get('topics'):
        print(f"Topics: {profile['topics']}")
    print(f"Created at: {profile['created_at']}")
    print(f"Updated at: {profile['updated_at']}")
else:
    print("No profile found")
```
### 4. profile_list() — 列出用户档案并进行筛选 {#4-profile_list--list-user-profiles-with-filtering}

获取用户档案列表，可选择按主题进行筛选。

#### 签名 {#signature-3}
```python
def profile_list(
    self,
    user_id: Optional[str] = None,
    main_topic: Optional[List[str]] = None,
    sub_topic: Optional[List[str]] = None,
    topic_value: Optional[List[str]] = None,
    limit: Optional[int] = 100,
    offset: Optional[int] = 0,
) -> List[Dict[str, Any]]
```
#### 参数 {#parameters-3}

- `user_id` (str, optional): 用于筛选的用户标识符
- `main_topic` (List[str], optional): 主主题名称列表，用于筛选
- `sub_topic` (List[str], optional): 子主题路径列表，用于筛选。每个路径应采用格式 "main_topic.sub_topic"，例如：["basic_information.user_name"]
- `topic_value` (List[str], optional): 按精确匹配筛选的主题值列表
- `limit` (int, optional): 返回的配置文件数量限制（默认值：100）
- `offset` (int, optional): 分页偏移量（默认值：0）

#### 返回值 {#return-value-3}

返回一个包含以下键的配置文件字典列表：
```python
[
    {
        "id": 1,                               # 档案 ID
        "user_id": "user_001",                 # 用户 ID
        "profile_content": "...",              # 档案内容（文本，可用时）
        "topics": {...},                       # 结构化 topic 字典（如果可用）
        "created_at": "2024-01-01T00:00:00",   # 创建时间（ISO 格式）
        "updated_at": "2024-01-01T00:00:00"    # 最后更新时间（ISO 格式）
    },
    ...
]
```
如果未找到任何配置文件，则返回空列表。

#### 示例 {#examples-3}
```python
# 获取所有档案
all_profiles = user_memory.profile_list()

# 获取指定用户的档案
user_profiles = user_memory.profile_list(user_id="user_001")

# 按主 topic 过滤
profiles = user_memory.profile_list(main_topic=["basic_information", "professional"])

# 按子 topic 过滤
profiles = user_memory.profile_list(sub_topic=["basic_information.user_name", "professional.occupation"])
```
### 5. delete_profile() — 删除用户档案 {#5-delete_profile--delete-user-profile}

通过 user_id 删除用户档案。

#### 签名 {#signature-4}
```python
def delete_profile(
    self,
    user_id: str,
) -> bool
```
#### 参数 {#parameters-4}

- `user_id` (str, 必填): 用户标识符

#### 返回值 {#return-value-4}

如果个人资料删除成功，返回 `True`；如果未找到个人资料，返回 `False`。

#### 示例 {#examples-4}
```python
# 删除用户档案
deleted = user_memory.delete_profile(user_id="user_001")

if deleted:
    print("Profile deleted successfully")
else:
    print("Profile not found")
```
## 完整示例 {#complete-example}

以下是一个完整的示例，展示了 `UserMemory` 的主要功能：
```python
from powermem import UserMemory, auto_config

# 初始化
config = auto_config()
user_memory = UserMemory(config=config)

# 1. 添加初始对话并提取档案
conversation1 = [
    {"role": "user", "content": "Hello, I'm Li, a data scientist specializing in machine learning."},
    {"role": "assistant", "content": "Nice to meet you, Li! Machine learning is a promising field."}
]

result1 = user_memory.add(
    messages=conversation1,
    user_id="user_002",
)

print("=== First conversation ===")
print(f"Profile extracted: {result1.get('profile_extracted', False)}")
if result1.get('profile_content'):
    print(f"Profile content: {result1['profile_content']}")

# 2. 添加更多对话并更新档案
conversation2 = [
    {"role": "user", "content": "I like reading tech blogs and often attend tech conferences."},
    {"role": "assistant", "content": "Sounds like you're passionate about learning new technologies!"}
]

result2 = user_memory.add(
    messages=conversation2,
    user_id="user_002",
)

print("\n=== Second conversation ===")
print(f"Profile updated: {result2.get('profile_extracted', False)}")
if result2.get('profile_content'):
    print(f"Updated profile: {result2['profile_content']}")

# 3. 获取完整用户档案
profile = user_memory.profile(
    user_id="user_002",
)

print("\n=== Full user profile ===")
if profile:
    print(f"Profile ID: {profile['id']}")
    print(f"Profile content: {profile['profile_content']}")
    print(f"Last updated: {profile['updated_at']}")

# 4. 搜索记忆并包含用户档案
search_results = user_memory.search(
    query="user's work and interests",
    user_id="user_002",
    limit=5,
    add_profile=True  # 包含用户档案
)

print("\n=== 搜索结果 ===")
if 'profile_content' in search_results:
    print(f"User profile: {search_results['profile_content']}\n")

print(f"Found {len(search_results.get('results', []))} related memories:")
for i, result in enumerate(search_results.get('results', []), 1):
    print(f"{i}. {result['memory']} (score: {result.get('score', 0):.2f})")
```
## 最佳实践 {#best-practices}

1. **始终提供 `user_id`**：`add()` 方法需要 `user_id`；确保每个用户都有唯一的标识符

2. **使用 `agent_id` 区分 Agents**：在Multi-Agent 场景中，使用 `agent_id` 来区分不同的配置文件和记忆

3. **适当使用 `run_id`**：使用 `run_id` 区分会话或运行，以便更精确地管理配置文件

4. **定期检查配置文件**：使用 `profile()` 定期检查并确保配置文件信息的准确性

5. **在需要时包含配置文件**：当额外的上下文有用时，设置 `add_profile=True` 以在搜索结果中包含用户配置文件

6. **处理空配置文件**：如果 `profile()` 返回 `{}`，说明尚未提取任何配置文件；首先使用对话数据调用 `add()`

## 与记忆的关系 {#relationship-with-memory}

`UserMemory` 使用内部的 `Memory` 实例来存储对话事件，因此：

- `UserMemory` 支持 `Memory` 的所有配置选项
- `UserMemory.add()` 和 `UserMemory.search()` 调用底层的 `Memory` 方法
- `UserMemory` 在 `Memory` 的基础上增加了用户配置文件管理功能
- 如果需要，可以直接访问 `user_memory.memory` 来使用底层的 `Memory` 实例

## 相关文档 {#related-documents}

- [快速入门](0001-getting_started.md) — 学习 PowerMem 的基础知识
- [配置指南](0003-configuration.md) — 详细的配置说明
- [Multi-Agent 指南](0005-multi_agent.md) — 使用多个 Agent
