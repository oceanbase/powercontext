# Agent API 参考 {#agent-apis-reference}

powermem 提供了全面的Multi-Agent 记忆管理功能。

## Agent 记忆管理 {#agent-memory-management}

### 创建特定 Agent 的记忆 {#creating-agent-specific-memories}
```python
from powermem import Memory, auto_config

config = auto_config()

# 为不同 Agent 创建 Memory 实例
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")
```
### 特定 Agent 的操作 {#agent-specific-operations}

#### 添加 Agent 记忆 {#adding-agent-memories}
```python
# Support Agent 添加记忆
support_agent.add(
    messages="Customer prefers email support",
    user_id="customer123",
    agent_id="support_agent",
    metadata={"category": "preference"}
)

# Sales Agent 添加记忆
sales_agent.add(
    messages="Customer interested in AI features",
    user_id="customer123",
    agent_id="sales_agent",
    metadata={"category": "interest"}
)
```
#### 特定 Agent 的搜索 {#agent-specific-search}
```python
# 仅搜索 Support Agent 的记忆
results = support_agent.search(
    query="customer preferences",
    user_id="customer123",
    agent_id="support_agent"  # 按 Agent 过滤
)
```
#### 跨 Agent 搜索 {#cross-agent-search}
```python
# 跨所有 Agent 搜索
results = support_agent.search(
    query="customer information",
    user_id="customer123"
    # 不传 agent_id 过滤，搜索所有 Agent
)
```
## Multi-Agent 记忆管理器 {#multi-agent-memory-manager}

### 使用MultiAgentMemoryManager {#using-multiagentmemorymanager}
```python
from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager
from powermem.agent.components.collaboration_coordinator import CollaborationCoordinator
from powermem.agent.components.permission_controller import PermissionController

config = auto_config()

# 创建 Multi-Agent 管理器
manager = MultiAgentMemoryManager(config=config)

# 注册 Agent
manager.register_agent("alice", "developer")
manager.register_agent("bob", "developer")
manager.register_agent("charlie", "qa")
```
### Agent 组件 {#agent-components}

#### Collaboration Coordinator {#collaboration-coordinator}

管理 Agent 的协作和共享记忆。
```python
from powermem.agent.components.collaboration_coordinator import CollaborationCoordinator

coordinator = CollaborationCoordinator(config=config)

# 跟踪协作
coordinator.track_collaboration(
    agent_ids=["alice", "bob"],
    memory_id="memory_123",
    context="Working on API integration"
)
```
#### 权限控制器 {#permission-controller}

控制对 Agent 记忆的访问权限。
```python
from powermem.agent.components.permission_controller import PermissionController

permission = PermissionController(config=config)

# 设置权限
permission.set_permission(
    agent_id="alice",
    memory_id="memory_123",
    permission="READ_WRITE"
)

# 检查访问权限
can_access = permission.check_access(
    agent_id="bob",
    memory_id="memory_123"
)
```
#### 隐私保护器 {#privacy-protector}

管理隐私级别和数据保护。
```python
from powermem.agent.components.privacy_protector import PrivacyProtector

privacy = PrivacyProtector(config=config)

# 设置隐私级别
privacy.set_privacy_level(
    memory_id="memory_123",
    level="PRIVATE"
)

# 检查记忆是否可共享
can_share = privacy.can_share(
    memory_id="memory_123",
    target_agent_id="bob"
)
```
#### Scope Controller {#scope-controller}

管理记忆范围（AGENT、USER、GROUP 等）。
```python
from powermem.agent.components.scope_controller import ScopeController

scope = ScopeController(config=config)

# 设置记忆 scope
scope.set_scope(
    memory_id="memory_123",
    scope="AGENT"
)

# 按 scope 获取记忆
agent_memories = scope.get_by_scope(
    agent_id="alice",
    scope="AGENT"
)
```
## 记忆范围 {#memory-scopes}

powermem 支持不同的记忆范围：

- **AGENT**: 特定 Agent 的记忆
- **USER**: 特定用户的记忆
- **GROUP**: 群组/共享记忆
- **SYSTEM**: 系统范围的记忆

### 设置记忆范围 {#setting-memory-scope}
```python
memory.add(
    messages="Project status update",
    user_id="alice",
    agent_id="alice_dev",
    metadata={"scope": "AGENT"}
)
```
### 基于Scope的搜索 {#scope-based-search}
```python
# 仅搜索 Agent scope 的记忆
results = memory.search(
    query="project status",
    user_id="alice",
    filters={"scope": "AGENT"}
)
```
## 多用户记忆管理器 {#multi-user-memory-manager}

针对多用户场景：
```python
from powermem.agent.implementations.multi_user import MultiUserMemoryManager

config = auto_config()

# 创建多用户管理器
manager = MultiUserMemoryManager(config=config)

# 添加用户特定记忆
manager.add_user_memory(
    user_id="user123",
    messages="User preference",
    metadata={"category": "preference"}
)

# 搜索用户记忆
results = manager.search_user_memories(
    user_id="user123",
    query="preferences"
)
```
## 混合记忆管理器 {#hybrid-memory-manager}

针对结合Multi-Agent 和多用户的混合场景：
```python
from powermem.agent.implementations.hybrid import HybridMemoryManager

config = auto_config()

# 创建混合管理器
manager = HybridMemoryManager(config=config)

# 添加同时包含 Agent 和用户上下文的记忆
manager.add(
    messages="Collaborative memory",
    user_id="user123",
    agent_id="agent456",
    metadata={"scope": "GROUP"}
)
```
## 最佳实践 {#best-practices}

1. **一致使用 agent_id**：在创建记忆时始终指定 agent_id
2. **适当设置 scope**：使用适当的记忆 scope 进行隔离
3. **设置权限**：为敏感记忆配置权限
4. **跟踪协作**：使用协作协调器进行共享工作
5. **默认隐私保护**：设置适当的隐私级别

## 另请参阅 {#see-also}

- [Multi-Agent 指南](../guides/0005-multi_agent.md)
- [Memory API](./0001-memory.md)