---
title: Multi-Agent 指南
sidebar_label: Multi-Agent 指南
---

## 前置条件 {#prerequisites}

- Python 3.11+
- 已安装 powermem（`pip install powermem`）
- 对 powermem 有基本了解（参见[入门指南](./0001-getting_started.md)）

## 什么是 Multi-Agent Memory？ {#what-is-multi-agent-memory}

Multi-Agent Memory 允许为不同的 AI Agent 创建独立的记忆空间，同时在需要时实现协作和信息共享。这对于多个专业化 Agent 协同工作的应用场景至关重要，例如：

- **客户支持系统**：不同的 Agent 处理不同的方面（支持、销售、技术）
- **开发团队**：多个开发者或 AI Agent 在同一项目上协作
- **企业应用**：不同部门或团队拥有独立但相互关联的记忆
- **复杂工作流**：需要维护自身上下文的 Agent，同时访问共享信息

### 核心概念 {#key-concepts}

**记忆隔离**：每个 Agent 拥有自己的记忆空间，防止一个 Agent 的记忆干扰另一个 Agent。

**跨 Agent 协作**：Agent 可以在需要时搜索所有记忆，从而实现信息共享和协作。

**Agent 标识**：每个记忆都带有一个 `agent_id` 标签，用于跟踪哪个 Agent 创建或拥有该记忆。

**灵活搜索**：根据需求，可以在单个 Agent 的记忆中搜索，也可以跨所有 Agent 的记忆进行搜索。

## 配置 {#configuration}

在使用 Multi-Agent Memory 之前，需要配置 powermem。Powermem 可以自动从项目目录中的 `.env` 文件加载配置。这是为您的使用场景配置 powermem 的推荐方式。

### 创建 .env 文件 {#creating-a-env-file}

1. 复制示例配置文件：
   ```bash
   cp .env.example .env
   ```
2. 编辑 `.env` 文件并配置您的设置：
   - **LLM Provider**: 选择您的语言模型提供商（Qwen、OpenAI、Anthropic 等）
   - **Embedding Provider**: 选择文本如何转换为向量
   - **Vector Store**: 选择您的数据库（开发环境使用 SQLite，生产环境使用 OceanBase）

> **注意：** 当您调用 `auto_config()` 时，PowerMem 将自动：
> - 在当前目录中查找 `.env` 文件
> - 从环境变量中加载配置
> - 如果未找到配置，则使用合理的默认值

有关更多配置选项，请参阅 `.env.example` 中的完整示例或参考 [配置指南](./0003-configuration.md)。

### 使用 JSON/字典配置 {#using-jsondictionary-configuration}

除了使用 `.env` 文件，您还可以直接以 Python 字典（类似 JSON 的格式）传递配置。这在以下情况下非常有用：
- 您希望从 JSON 文件加载配置
- 您需要以编程方式生成配置
- 您将配置嵌入到应用程序代码中
- 您需要为不同的 Agent 使用不同的配置

以下是一个使用字典配置Multi-Agent 场景的示例：
```python
from powermem import Memory

# 将配置定义为字典（类似 JSON 的格式）
config = {
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'qwen-plus',
            'temperature': 0.7
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
            },
            'embedding_model_dims': 1536,
            'index_type': 'IVF_FLAT',
            'vidx_metric_type': 'cosine'
        }
    }
}

# 使用相同配置创建多个 Agent
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

print("✓ Created agents with JSON config!")
```
> **注意：** 在Multi-Agent 场景中使用字典/JSON配置时：
> - 所有 Agent 通常共享相同的配置（数据库、LLM、嵌入）
> - 每个 Agent 通过其唯一的 `agent_id` 参数区分
> - 相同的配置可以重复用于所有 Agent 以确保一致性
> - 确保包含所有必需字段（`llm`、`embedder`、`vector_store`），以及它们各自的 `provider` 和 `config` 部分

## 理解 Multi-Agent 记忆架构 {#understanding-multi-agent-memory-architecture}

PowerMem中的Multi-Agent 记忆基于以下几个关键原则：

### 记忆隔离 {#memory-isolation}

每个 Agent 在其自己的隔离记忆空间中运行。当您使用 `agent_id` 创建一个 `Memory` 实例时，通过该实例添加的所有记忆都会自动标记为该 Agent 的ID。这确保了：

- **隐私**：Agent 无法意外访问彼此的私人记忆
- **组织性**：记忆根据 Agent 清晰地组织
- **安全性**：敏感信息可以保存在特定 Agent 的上下文中

### Agent 标识 {#agent-identification}

`agent_id` 参数在Multi-Agent 场景中至关重要：

- **唯一标识符**：为每个 Agent 使用描述性且唯一的名称（例如，`"support_agent"`，`"sales_agent"`）
- **一致性使用**：在整个应用程序中始终为同一个 Agent 使用相同的 `agent_id`
- **命名约定**：使用清晰、一致的命名（例如，`"agent_type_role"` 格式）

### 跨 Agent 搜索 {#cross-agent-search}

虽然记忆默认是隔离的，但PowerMem允许在需要时跨所有 Agent 进行搜索：

- **协作**：Agent 可以在适当时访问共享信息
- **上下文构建**：从多个 Agent 的视角构建全面的上下文
- **灵活访问**：控制搜索是特定于 Agent 还是跨 Agent 的

### 记忆范围 {#memory-scopes}

记忆范围提供了对记忆可见性和组织的额外控制：

- **AGENT范围**：特定于某个 Agent 的记忆
- **USER范围**：特定于某个用户的记忆
- **GROUP范围**：在组内共享的记忆
- **自定义范围**：根据您的使用场景定义自己的范围类型

## 创建多个 Agent {#creating-multiple-agents}

要使用Multi-Agent 记忆，您需要为每个 Agent 创建单独的 `Memory` 实例。每个实例应具有唯一的 `agent_id` 来标识该 Agent。

### 理解 Agent 创建 {#understanding-agent-creation}

当您使用 `agent_id` 创建一个 `Memory` 实例时：

1. **实例创建**：使用指定的 `agent_id` 创建 `Memory` 实例
2. **自动标记**：通过该实例添加的所有记忆都会自动标记为该 `agent_id`
3. **隔离**：记忆默认隔离到该 Agent
4. **共享配置**：所有 Agent 可以共享相同的配置（数据库、LLM、嵌入）

### Agent 创建的最佳实践 {#best-practices-for-agent-creation}

- **使用描述性名称**：选择清晰、描述性的 `agent_id` 值（例如，`"support_agent"` 而不是 `"agent1"`）
- **一致的命名**：建立命名约定并坚持使用
- **重用实例**：创建 Agent 实例一次，并在整个应用程序中重用它们
- **配置共享**：为所有 Agent 使用相同的配置以确保一致性

### 创建 Agent 实例 {#creating-agent-instances}

以下是为不同 Agent 创建记忆实例的方法：
```python
from powermem import Memory, auto_config

config = auto_config()

# 为不同 Agent 创建 Memory 实例
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

print("✓ Created memory instances for:")
print("  - Support Agent")
print("  - Sales Agent")
print("  - Technical Agent")
```
## 添加特定 Agent 的记忆 {#adding-agent-specific-memories}

每个 Agent 都维护其自己的记忆空间。当一个 Agent 添加记忆时，它会自动与该 Agent 的 `agent_id` 关联。这确保了正确的隔离和组织。

### 特定 Agent 记忆的工作原理 {#how-agent-specific-memories-work}

当你在一个 `Memory` 实例上使用带有 `agent_id` 的 `add()` 方法时：

1. **自动标记**：记忆会自动标记为该实例的 `agent_id`
2. **隔离**：记忆存储在该 Agent 的独立空间中
3. **搜索行为**：默认情况下，搜索只会找到该 Agent 的记忆
4. **元数据**：`agent_id` 作为元数据存储，支持灵活查询

### 何时使用特定 Agent 的记忆 {#when-to-use-agent-specific-memories}

- **专业知识**：每个 Agent 拥有特定领域的信息
- **隐私需求**：仅特定 Agent 可访问的信息
- **组织性**：将不同类型的信息分开存储
- **上下文管理**：为不同的 Agent 角色维护独立的上下文

### 最佳实践 {#best-practices}

- **一致的 user_id**：对同一用户在所有 Agent 中使用相同的 `user_id`
- **有意义的元数据**：添加元数据以帮助组织和筛选记忆
- **清晰的内容**：编写清晰且自包含的记忆内容
- **定期更新**：随着信息变化，保持 Agent 记忆的更新

让我们看看每个 Agent 如何将记忆添加到它们自己的空间中：
```python
from powermem import Memory, auto_config

config = auto_config()
customer_id = "customer_12345"

# 创建 Agent
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

# 客服 Agent 添加记忆
support_agent.add(
    "Customer prefers email support over phone calls",
    user_id=customer_id,
    metadata={"category": "communication_preference"}
)

# 销售 Agent 添加记忆
sales_agent.add(
    "Customer interested in AI-powered features and automation",
    user_id=customer_id,
    metadata={"category": "product_interest"}
)

# 技术 Agent 添加记忆
tech_agent.add(
    "Customer uses Python and PostgreSQL in their tech stack",
    user_id=customer_id,
    metadata={"category": "technical_info"}
)

print("✓ Added memories for each agent")
```
## 特定 Agent 的搜索 {#agent-specific-search}

默认情况下，当您使用带有 `agent_id` 的 `Memory` 实例进行搜索时，搜索范围仅限于该 Agent 的记忆。这种方式提供了隔离性，并确保 Agent 仅能看到与其自身上下文相关的信息。

### 理解特定 Agent 的搜索 {#understanding-agent-specific-search}

当您在 `Memory` 实例上调用 `search()` 时：

1. **默认行为**：如果实例具有 `agent_id`，搜索会自动过滤到该 Agent
2. **显式过滤**：您也可以在搜索参数中显式指定 `agent_id`
3. **隔离性**：结果仅包含指定 Agent 的记忆
4. **性能**：特定 Agent 的搜索通常更快，因为它们只搜索较小的记忆子集

### 何时使用特定 Agent 的搜索 {#when-to-use-agent-specific-search}

- **上下文检索**：获取特定 Agent 上下文的相关信息
- **隐私**：确保 Agent 仅能看到自己的记忆
- **性能**：当您只需要一个 Agent 的记忆时，搜索速度更快
- **聚焦结果**：获取特定于某个 Agent 领域的结果

### 搜索参数 {#search-parameters}

`search()` 方法支持多个参数用于特定 Agent 的搜索：

- **`query`**：搜索查询（必填）
- **`user_id`**：按用户过滤（可选，但推荐）
- **`agent_id`**：显式按 Agent 过滤（可选，默认为实例的 `agent_id`）
- **`limit`**：结果的最大数量（默认值：10）
- **`filters`**：额外的元数据过滤器（可选）

### 最佳实践 {#best-practices-1}

- **始终指定 user_id**：对于多用户应用程序，始终在搜索中包含 `user_id`
- **使用语义查询**：编写描述您要查找内容的自然语言查询
- **限制结果数量**：使用适当的 `limit` 值以避免结果过多
- **结合元数据使用**：使用元数据过滤器以实现更精确的搜索

以下是每个 Agent 如何搜索其自身记忆的方式：
```python
from powermem import Memory, auto_config

config = auto_config()
customer_id = "customer_12345"

# 创建 Agent
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")

# 添加记忆
support_agent.add(
    "Customer prefers email support",
    user_id=customer_id
)
sales_agent.add(
    "Customer interested in AI features",
    user_id=customer_id
)

# 客服 Agent 搜索自己的记忆
print("Support Agent Search:")
support_results = support_agent.search(
    query="customer preferences",
    user_id=customer_id,
    agent_id="support_agent"
)
for result in support_results.get('results', []):
    print(f"  - {result['memory']}")

# 销售 Agent 搜索自己的记忆
print("\nSales Agent Search:")
sales_results = sales_agent.search(
    query="customer interests",
    user_id=customer_id,
    agent_id="sales_agent"
)
for result in sales_results.get('results', []):
    print(f"  - {result['memory']}")
```
## 跨 Agent 搜索 {#cross-agent-search-1}

虽然特定 Agent 的搜索提供了隔离性，但有时您需要在所有 Agent 的记忆中进行搜索。这种方式可以实现协作，并允许 Agent 访问共享信息。

### 理解跨 Agent 搜索 {#understanding-cross-agent-search}

跨 Agent 搜索可以让您：

1. **访问所有记忆**：在所有 Agent 的记忆中进行搜索
2. **构建全面上下文**：从多个 Agent 的视角收集信息
3. **实现协作**：让 Agent 了解其他 Agent 所掌握的信息
4. **灵活查询**：选择何时搜索所有 Agent 与特定 Agent

### 跨 Agent 搜索的工作原理 {#how-cross-agent-search-works}

要执行跨 Agent 搜索：

1. **省略agent_id**：在搜索参数中不指定`agent_id`，或者将其设置为`None`
2. **搜索所有 Agent**：搜索将查询所有 Agent 的记忆
3. **结果标记**：结果中包含`agent_id`，以便您知道每条记忆是由哪个 Agent 创建的
4. **过滤**：您仍然可以通过`user_id`和其他元数据进行过滤

### 何时使用跨 Agent 搜索 {#when-to-use-cross-agent-search}

- **全面上下文**：从多个 Agent 的视角构建完整的上下文
- **协作**：当 Agent 需要了解其他 Agent 所学内容时
- **分析**：分析所有 Agent 的记忆信息
- **共享知识**：访问所有 Agent 都应可见的记忆

### 最佳实践 {#best-practices-2}

- **谨慎使用**：仅在确实需要所有 Agent 的信息时使用跨 Agent 搜索
- **检查结果中的agent_id**：始终检查结果中的`agent_id`以了解信息来源
- **结合过滤器使用**：使用`user_id`和元数据过滤器来缩小结果范围
- **性能考虑**：跨 Agent 搜索可能会较慢，因为它需要搜索更多记忆

### 使用场景 {#use-cases}

- **客户支持**：支持 Agent 需要了解销售和技术 Agent 对客户的了解
- **项目管理**：开发人员需要查看QA和其他开发人员记录的内容
- **分析**：分析所有 Agent 记忆中的模式
- **知识共享**：在 Agent 之间共享重要信息

以下是如何搜索所有 Agent 的方法：
```python
from powermem import Memory, auto_config

config = auto_config()
customer_id = "customer_12345"

# 创建 Agent
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

# 为每个 Agent 添加记忆
support_agent.add("Customer prefers email support", user_id=customer_id)
sales_agent.add("Customer interested in AI features", user_id=customer_id)
tech_agent.add("Customer uses Python and PostgreSQL", user_id=customer_id)

# 跨 Agent 搜索（不加 agent_id 过滤）
print("Cross-Agent Search:")
all_results = support_agent.search(
    query="customer information",
    user_id=customer_id
    # 不传 agent_id：搜索所有 Agent 的记忆
)

print(f"Found {len(all_results.get('results', []))} memories across all agents:")
for result in all_results.get('results', []):
    agent_id = result.get('agent_id', 'Unknown')
    print(f"  [{agent_id}] {result['memory']}")
```
## Multi-Agent 的项目协作 {#project-collaboration-with-multiple-agents}

在实际场景中，多个 Agent 通常会在同一个项目或任务上协作。PowerMem的Multi-Agent 记忆系统通过`run_id`和元数据支持这一点，使您能够按项目组织记忆，同时保持 Agent 的隔离性。

### 理解项目协作 {#understanding-project-collaboration}

当多个 Agent 在同一个项目上工作时：

1. **共享上下文**：使用`run_id`按项目或任务对记忆进行分组
2. **Agent 隔离**：每个 Agent 仍然保持自己的记忆空间
3. **项目范围搜索**：在所有 Agent 的记忆中搜索特定项目
4. **元数据组织**：使用元数据按项目的不同方面对记忆进行分类

### 使用 run_id 进行项目组织 {#using-run_id-for-project-organization}

`run_id`参数允许您：

- **分组记忆**：将记忆与特定项目、任务或对话关联
- **项目范围查询**：在所有 Agent 中搜索特定项目的所有记忆
- **上下文管理**：为长期项目或对话维护上下文
- **组织**：按项目生命周期或工作流阶段组织记忆

### 项目协作的最佳实践 {#best-practices-for-project-collaboration}

- **一致的run_id**：对与同一项目相关的所有记忆使用相同的`run_id`
- **描述性ID**：使用清晰、描述性的`run_id`值（例如，`"project_ai_platform"`，`"customer_onboarding_12345"`）
- **元数据丰富化**：添加元数据以对记忆进行分类（例如，`scope`，`module`，`issue_type`）
- **定期更新**：随着工作的进展，保持项目记忆的更新

### 使用场景 {#use-cases-1}

- **软件开发**：多个开发人员和QA在同一个项目上工作
- **客户入职**：多个 Agent（销售、支持、技术）与同一客户合作
- **研究项目**：多个研究人员在同一研究主题上协作
- **内容创作**：多个 Agent 在同一内容的不同方面工作

以下是多个 Agent 在同一项目上协作的示例：
```python
from powermem import Memory, auto_config

config = auto_config()
project_id = "project_ai_platform"

# 创建开发者 Agent
alice_dev = Memory(config=config, agent_id="alice_dev")
bob_dev = Memory(config=config, agent_id="bob_dev")
charlie_qa = Memory(config=config, agent_id="charlie_qa")

# Alice 添加开发记忆
alice_dev.add(
    "Implemented user authentication module with JWT tokens",
    user_id="alice",
    run_id=project_id,
    metadata={"scope": "development", "module": "authentication"}
)

# Bob 添加开发记忆
bob_dev.add(
    "Created database schema for user profiles",
    user_id="bob",
    run_id=project_id,
    metadata={"scope": "development", "module": "database"}
)

# Charlie 添加 QA 记忆
charlie_qa.add(
    "Found critical bug in user registration flow",
    user_id="charlie",
    run_id=project_id,
    metadata={"scope": "testing", "issue_type": "bug"}
)

# 搜索项目范围内的记忆
print("Project Status Search:")
project_results = alice_dev.search(
    query="project status and progress",
    run_id=project_id
)

for result in project_results.get('results', []):
    agent_id = result.get('agent_id', 'Unknown')
    scope = result.get('metadata', {}).get('scope', 'Unknown')
    print(f"  [{agent_id}] [{scope}] {result['memory']}")
```
## 记忆范围 {#memory-scopes-1}

记忆范围为记忆的可见性和组织提供了额外的控制层。虽然 `agent_id` 按 Agent 隔离记忆，但范围允许您以更细粒度的方式控制可见性。

### 理解记忆范围 {#understanding-memory-scopes}

记忆范围是控制以下内容的元数据值：

- **可见性**：谁可以查看和访问记忆
- **组织**：如何对记忆进行分类和分组
- **访问控制**：对记忆访问的细粒度控制
- **上下文**：关于记忆用途的附加上下文

### 常见范围类型 {#common-scope-types}

PowerMem 支持多种范围类型，您也可以定义自定义范围：

- **AGENT**：记忆特定于某个 Agent（限制最严格）
- **USER**：记忆特定于某个用户
- **GROUP**：记忆在组内共享
- **GLOBAL**：记忆对所有人可访问（限制最少）
- **Custom**：根据您的使用场景定义自定义范围类型

### 如何使用记忆范围 {#how-to-use-memory-scopes}

1. **在元数据中添加范围**：在添加记忆时，将 `scope` 包含在 `metadata` 参数中
2. **按范围过滤**：在搜索中使用元数据过滤器按范围进行过滤
3. **与 agent_id 结合使用**：同时使用 `agent_id` 和范围实现细粒度控制
4. **灵活查询**：按范围、Agent、用户或组合查询记忆

### 最佳实践 {#best-practices-3}

- **一致的范围值**：在您的应用程序中使用一致的范围值
- **记录范围含义**：记录每种范围类型在您的应用程序中的含义
- **与 agent_id 结合使用**：将范围与 `agent_id` 结合使用以实现最大灵活性
- **适当过滤**：使用范围过滤器缩小搜索结果

### 使用场景 {#use-cases-2}

- **访问控制**：控制哪些 Agents 或用户可以查看特定记忆
- **组织**：按可见性级别组织记忆
- **隐私**：通过范围管理实现隐私控制
- **工作流管理**：使用范围管理不同的工作流阶段

以下是如何使用记忆范围控制可见性的方法：
```python
from powermem import Memory, auto_config

config = auto_config()

# 创建 Agent
agent = Memory(config=config, agent_id="demo_agent")

# 添加不同 scope 的记忆
agent.add(
    "Agent-specific memory",
    user_id="user123",
    metadata={"scope": "AGENT"}
)

agent.add(
    "User-specific memory",
    user_id="user123",
    metadata={"scope": "USER"}
)

agent.add(
    "Group memory",
    user_id="user123",
    metadata={"scope": "GROUP"}
)

# 使用 filters 参数按范围搜索
# 注意：嵌套 metadata 字段需要使用类似 "metadata.scope" 的 key path
print("Agent-scoped memories:")
results = agent.search(
    query="memories",
    user_id="user123",
    filters={"metadata.scope": "AGENT"}
)
for result in results.get('results', []):
    print(f"  - {result['memory']}")

# 替代方案：搜索所有记忆后在 Python 中过滤
print("\nAll memories:")
all_results = agent.search(
    query="memories",
    user_id="user123"
)
for result in all_results.get('results', []):
    scope = result.get('metadata', {}).get('scope', 'Unknown')
    print(f"  [{scope}] {result['memory']}")
```
## 记忆隔离与验证 {#memory-isolation-and-verification}

记忆隔离是Multi-Agent 记忆的一个基本特性。它确保每个 Agent 的记忆是独立的，防止干扰并维护隐私。

### 理解记忆隔离 {#understanding-memory-isolation}

记忆隔离意味着：

1. **独立存储**：每个 Agent 的记忆是独立存储的（使用 `agent_id` 标记）
2. **默认过滤**：搜索会自动过滤到 Agent 自己的记忆
3. **隐私保护**：Agent 无法意外访问其他 Agent 的记忆
4. **组织清晰**：明确的组织和关注点分离

### 隔离如何工作 {#how-isolation-works}

当你使用一个 `agent_id` 创建一个 `Memory` 实例时：

- **自动标记**：所有记忆都会被标记上对应的 `agent_id`
- **默认搜索范围**：搜索默认只针对该 Agent 的记忆
- **显式过滤**：你可以在搜索中显式地按 `agent_id` 进行过滤
- **跨 Agent 访问**：在需要时，你仍然可以通过跨 Agent 搜索访问其他 Agent 的记忆

### 验证隔离 {#verifying-isolation}

验证记忆隔离是否正常工作非常重要：

1. **添加记忆**：每个 Agent 将记忆添加到自己的空间
2. **独立搜索**：每个 Agent 仅搜索自己的记忆
3. **验证结果**：确认 Agent 只能看到自己的记忆
4. **测试跨 Agent**：验证跨 Agent 搜索在需要时是否正常工作

### 最佳实践 {#best-practices-4}

- **测试隔离**：定期验证记忆隔离是否按预期工作
- **使用一致的 agent_id**：始终为同一个 Agent 使用相同的 `agent_id`
- **监控访问**：记录或监控记忆访问以确保正确的隔离
- **记录行为**：记录隔离在你的应用程序中的工作方式

### 隔离问题排查 {#troubleshooting-isolation-issues}

如果你发现隔离问题：

- **检查 agent_id**：验证 `agent_id` 是否被正确设置
- **验证标记**：确认记忆是否被正确标记了对应的 `agent_id`
- **检查搜索参数**：确保搜索参数正确地按 `agent_id` 进行过滤
- **检查元数据**：验证 `agent_id` 是否存在于记忆的元数据中

以下是验证记忆是否按 Agent 正确隔离的方法：
```python
from powermem import Memory, auto_config

config = auto_config()
user_id = "user123"

# 创建两个 Agent
agent1 = Memory(config=config, agent_id="agent1")
agent2 = Memory(config=config, agent_id="agent2")

# Agent1 添加记忆
agent1.add("Agent1's memory", user_id=user_id)

# Agent2 添加记忆
agent2.add("Agent2's memory", user_id=user_id)

# Agent1 搜索（应只看到自己的记忆）
print("Agent1 search:")
results1 = agent1.search(query="memories", user_id=user_id, agent_id="agent1")
for result in results1.get('results', []):
    print(f"  - {result['memory']}")

# Agent2 搜索（应只看到自己的记忆）
print("\nAgent2 search:")
results2 = agent2.search(query="memories", user_id=user_id, agent_id="agent2")
for result in results2.get('results', []):
    print(f"  - {result['memory']}")
```
## 总结与最佳实践 {#summary-and-best-practices}

PowerMem 中的Multi-Agent 记忆提供了一种强大的方式来管理多个AI Agent 的记忆，同时保持隔离并支持协作。以下是关键概念和最佳实践的总结：

### 关键要点 {#key-takeaways}

1. **记忆隔离**：每个 Agent 都有自己独立的记忆空间，确保隐私和组织性
2. **Agent 标识**：使用唯一且具有描述性的`agent_id`值来标识每个 Agent
3. **灵活搜索**：根据需求选择 Agent 特定搜索或跨 Agent 搜索
4. **项目组织**：使用`run_id`按项目或任务组织记忆
5. **范围控制**：使用记忆范围实现细粒度的可见性控制

### 最佳实践清单 {#best-practices-checklist}

**Agent 管理：**
- ✅ 使用具有描述性且唯一的`agent_id`值
- ✅ 创建 Agent 实例后重复使用
- ✅ 使用一致的命名约定
- ✅ 在多个 Agent 之间共享配置以保持一致性

**记忆管理：**
- ✅ 对于同一用户，在所有 Agent 中始终使用一致的`user_id`
- ✅ 添加有意义的元数据以组织记忆
- ✅ 使用`run_id`进行项目或任务的组织
- ✅ 随着信息变化及时更新记忆

**搜索策略：**
- ✅ 默认使用 Agent 特定搜索以确保隔离
- ✅ 仅在需要协作时使用跨 Agent 搜索
- ✅ 在多用户应用中始终指定`user_id`
- ✅ 使用元数据过滤器进行精确搜索

**隔离与安全：**
- ✅ 验证记忆隔离是否正常工作
- ✅ 在需要时测试跨 Agent 访问
- ✅ 监控记忆访问模式
- ✅ 在应用中记录隔离行为

### 常见模式 {#common-patterns}

**模式1：客户支持系统**
```python
# 针对不同业务面创建多个 Agent
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

# 每个 Agent 维护自己的上下文
# 跨 Agent 搜索，获得完整客户视图
```
**模式 2：开发团队**
```python
# 多个开发者协作同一项目
dev_agent = Memory(config=config, agent_id="dev_agent")
qa_agent = Memory(config=config, agent_id="qa_agent")

# 使用 run_id 按项目分组
# 跨 Agent 搜索项目级上下文
```
**模式 3：企业部门**
```python
# 不同部门
hr_agent = Memory(config=config, agent_id="hr_agent")
finance_agent = Memory(config=config, agent_id="finance_agent")

# 隔离记忆并按需共享
# 使用 scope 做访问控制
```
