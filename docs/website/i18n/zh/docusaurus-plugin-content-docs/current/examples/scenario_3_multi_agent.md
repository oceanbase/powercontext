# 场景 3：Multi-Agent {#scenario-3-multi-agent}

本场景展示了如何使用 PowerMem 与多个 Agent 协作——创建特定于 Agent 的记忆、跨 Agent 协作以及记忆隔离。

## 前置条件 {#prerequisites}

- 完成场景 1
- 对Multi-Agent 系统有基本了解
- 已安装 PowerMem

## 理解 Multi-Agent 记忆 {#understanding-multi-agent-memory}

在Multi-Agent 场景中：
- 每个 Agent 拥有独立的记忆空间
- Agent 可以在需要时共享记忆
- 跨 Agent 搜索支持协作
- 记忆 scope 控制可见性

## 步骤 1：创建多个 Agent {#step-1-create-multiple-agents}

首先，为不同的 Agent 创建记忆实例：
```python
# multi_agent_example.py
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
**运行以下代码：**
```bash
python multi_agent_example.py
```
**预期输出：**
```
✓ Created memory instances for:
  - Support Agent
  - Sales Agent
  - Technical Agent
```
## 第 2 步：添加特定 Agent 的记忆 {#step-2-add-agent-specific-memories}

每个 Agent 将记忆添加到它们自己的空间：
```python
# multi_agent_example.py
from powermem import Memory, auto_config

config = auto_config()
customer_id = "customer_12345"

# 创建 Agent
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

# Support Agent 添加记忆
support_agent.add(
    "Customer prefers email support over phone calls",
    user_id=customer_id,
    metadata={"category": "communication_preference"}
)

# Sales Agent 添加记忆
sales_agent.add(
    "Customer interested in AI-powered features and automation",
    user_id=customer_id,
    metadata={"category": "product_interest"}
)

# Technical Agent 添加记忆
tech_agent.add(
    "Customer uses Python and PostgreSQL in their tech stack",
    user_id=customer_id,
    metadata={"category": "technical_info"}
)

print("✓ Added memories for each agent")
```
**运行以下代码：**
```bash
python multi_agent_example.py
```
**预期输出：**
```
✓ Added memories for each agent
```
## 第三步：特定 Agent 的搜索 {#step-3-agent-specific-search}

每个 Agent 只能搜索自己的记忆：
```python
# multi_agent_example.py
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

# Support Agent 搜索自己的记忆
print("Support Agent Search:")
support_results = support_agent.search(
    query="customer preferences",
    user_id=customer_id,
    agent_id="support_agent"
)
for result in support_results.get('results', []):
    print(f"  - {result['memory']}")

# Sales Agent 搜索自己的记忆
print("\nSales Agent Search:")
sales_results = sales_agent.search(
    query="customer interests",
    user_id=customer_id,
    agent_id="sales_agent"
)
for result in sales_results.get('results', []):
    print(f"  - {result['memory']}")
```
**运行以下代码：**
```bash
python multi_agent_example.py
```
**预期输出：**
```
Support Agent Search:
  - Customer prefers email support

Sales Agent Search:
  - Customer interested in AI features
```
## 第4步：跨 Agent 搜索 {#step-4-cross-agent-search}

通过省略 agent_id 来跨所有 Agent 搜索：
```python
# multi_agent_example.py
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
    # 不传 agent_id，搜索所有 Agent
)

print(f"Found {len(all_results.get('results', []))} memories across all agents:")
for result in all_results.get('results', []):
    agent_id = result.get('agent_id', 'Unknown')
    print(f"  [{agent_id}] {result['memory']}")
```
**运行此代码：**
```bash
python multi_agent_example.py
```
**预期输出：**
```
Cross-Agent Search:
Found 3 memories across all agents:
  [support_agent] Customer prefers email support
  [sales_agent] Customer interested in AI features
  [tech_agent] Customer uses Python and PostgreSQL
```
## 第五步：项目协作 {#step-5-project-collaboration}

多个 Agent 协同处理同一个项目：
```python
# multi_agent_example.py
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

# 搜索项目级记忆
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
**运行此代码：**
```bash
python multi_agent_example.py
```
**预期输出：**
```
Project Status Search:
  [alice_dev] [development] Implemented user authentication module with JWT tokens
  [bob_dev] [development] Created database schema for user profiles
  [charlie_qa] [testing] Found critical bug in user registration flow
```
## 第6步：记忆范围 {#step-6-memory-scopes}

使用记忆范围来控制可见性：
```python
# multi_agent_example.py
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

# 使用 filters 参数按 scope 搜索
# 注意：嵌套 metadata 字段需使用类似 "metadata.scope" 的 key path
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
**运行以下代码：**
```bash
python multi_agent_example.py
```
**预期输出：**
```
Agent-scoped memories:
  - Agent-specific memory
```
## 第7步：记忆隔离 {#step-7-memory-isolation}

验证记忆是否按 Agent 隔离：
```python
# multi_agent_example.py
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
**运行以下代码：**
```bash
python multi_agent_example.py
```
**预期输出：**
```
Agent1 search:
  - Agent1's memory

Agent2 search:
  - Agent2's memory
```
## 完整示例 {#complete-example}

以下是一个完整的 Multi-Agent 示例：
```python
# complete_multi_agent_example.py
from powermem import Memory, auto_config

def main():
    config = auto_config()
    customer_id = "customer_12345"

    print("=" * 80)
    print("Multi-Agent Memory Demo")
    print("=" * 80)

    # 创建 Agent
    support_agent = Memory(config=config, agent_id="support_agent")
    sales_agent = Memory(config=config, agent_id="sales_agent")
    tech_agent = Memory(config=config, agent_id="tech_agent")

    print("\n[Step 1] Adding Agent-Specific Memories")
    print("-" * 60)

    # 为每个 Agent 添加记忆
    support_agent.add(
        "Customer prefers email support over phone calls",
        user_id=customer_id,
        metadata={"category": "communication"}
    )

    sales_agent.add(
        "Customer interested in AI-powered features",
        user_id=customer_id,
        metadata={"category": "interest"}
    )

    tech_agent.add(
        "Customer uses Python and PostgreSQL",
        user_id=customer_id,
        metadata={"category": "technical"}
    )

    print("✓ Added memories for each agent")

    print("\n[Step 2] Agent-Specific Search")
    print("-" * 60)

    # Support Agent 搜索
    print("Support Agent:")
    support_results = support_agent.search(
        query="customer preferences",
        user_id=customer_id,
        agent_id="support_agent"
    )
    for result in support_results.get('results', []):
        print(f"  - {result['memory']}")

    print("\n[Step 3] Cross-Agent Search")
    print("-" * 60)

    # 跨 Agent 搜索
    all_results = support_agent.search(
        query="customer information",
        user_id=customer_id
    )

    print(f"Found {len(all_results.get('results', []))} memories across all agents:")
    for result in all_results.get('results', []):
        agent_id = result.get('agent_id', 'Unknown')
        print(f"  [{agent_id}] {result['memory']}")

    print("\n" + "=" * 80)
    print("Demo completed successfully!")
    print("=" * 80)

if __name__ == "__main__":
    main()
```
**运行以下代码：**
```bash
python complete_multi_agent_example.py
```
## 拓展练习 {#extension-exercises}

### 练习 1：团队协作 {#exercise-1-team-collaboration}

创建一个包含多位开发者的团队场景：
```python
config = auto_config()
project_id = "project_xyz"

dev1 = Memory(config=config, agent_id="dev1")
dev2 = Memory(config=config, agent_id="dev2")

# 每位开发者添加项目记忆
dev1.add("Implemented feature X", run_id=project_id)
dev2.add("Implemented feature Y", run_id=project_id)

# 搜索项目级记忆
results = dev1.search("project progress", run_id=project_id)
```
### 练习 2：客户服务团队 {#exercise-2-customer-service-team}

创建一个客户服务场景：
```python
config = auto_config()
customer_id = "customer_123"

agent1 = Memory(config=config, agent_id="cs_agent_1")
agent2 = Memory(config=config, agent_id="cs_agent_2")

# 两个 Agent 处理同一个客户
agent1.add("Customer reported issue A", user_id=customer_id)
agent2.add("Customer issue resolved", user_id=customer_id)

# 两个 Agent 都能看到客户历史
history = agent1.search("customer issues", user_id=customer_id)
```
### 练习 3：记忆范围 {#exercise-3-memory-scopes}

尝试不同的记忆范围：
```python
agent = Memory(config=config, agent_id="agent")

# 添加不同 scope 的记忆
agent.add("Private memory", metadata={"scope": "AGENT"})
agent.add("Shared memory", metadata={"scope": "GROUP"})

# 使用 filters 参数按 scope 搜索
# 注意：嵌套 metadata 字段需使用类似 "metadata.scope" 的 key path
private = agent.search("memory", filters={"metadata.scope": "AGENT"})
for result in private.get('results', []):
    print(f"  - {result['memory']}")

print("\nShared memories (GROUP scope):")
shared = agent.search("memory", filters={"metadata.scope": "GROUP"})
```
