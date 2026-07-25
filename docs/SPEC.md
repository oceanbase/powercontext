# SPEC — powermem issue batch fix (2026-07-25)

> **Phase 2 行为契约 Spec** — 基于 `requirements.md` (Phase 1) 的 7 个用户故事和 28 个 AC。
> 本文档定义每个 Issue 的功能契约（FC）、精确变更规范、NFR 映射和可测试性。

---

## 目录

- [FC-1: #1178 — Homepage CSS class undefined](#fc-1-1178--homepage-css-class-undefined)
- [FC-2: #1158 — NIM reranker missing exports](#fc-2-1158--nim-reranker-missing-exports)
- [FC-3: #1151 — OceanBase forget marker lost](#fc-3-1151--oceanbase-forget-marker-lost)
- [FC-4: #1143 — retention_score written as null](#fc-4-1143--retention_score-written-as-null)
- [FC-5: #1137 — Raw exceptions in API responses](#fc-5-1137--raw-exceptions-in-api-responses)
- [FC-6: #1141 — Six-dimension unified importance evaluation](#fc-6-1141--six-dimension-unified-importance-evaluation)
- [FC-7: #1149 — Ebbinghaus decay in update_memory_decay](#fc-7-1149--ebbinghaus-decay-in-update_memory_decay)
- [NFR 映射](#nfr-映射)
- [AC → FC 映射表](#ac--fc-映射表)
- [Gherkin 场景](#gherkin-场景)

---

## FC-1: #1178 — Homepage CSS class undefined

### 接口

| 属性 | 值 |
|------|-----|
| 触发条件 | 访客打开首页，Features 组件渲染 |
| 涉及文件 | `docs/website/src/components/Features/index.tsx:103` |
| 变更类型 | 一行 JSX 修复 |

### 输入 → 输出

| | 当前状态 | 期望状态 |
|--|---------|---------|
| **输入** | `${styles[`icon-${feature.key}`]}` 动态查找 CSS class | `${styles.icon}` 使用固定 class |
| **输出** | 浏览器渲染 `undefined` 作为 class 名 | 容器 div 仅包含 `styles.icon` class |

### 错误条件

| 条件 | 处理 |
|------|------|
| `styles.icon` 未定义 | CSS Modules 保证局部作用域，不会发生 |
| 其他组件使用类似模式 | 已排查确认无其他组件 |

### 前置条件 / 后置条件

- **前置**：`styles.icon` class 在 `styles.module.css` 中已定义
- **后置**：5 个 feature 图标容器 div 的 class 属性仅包含 `styles.icon`

### 不变量

- Feature 图标的视觉样式（蓝色圆形背景）不变
- 响应式布局行为不变（≤800px 单列）
- `feature.key` 数据结构不变

### 精确变更

```
文件: docs/website/src/components/Features/index.tsx
行号: 103
变更前: className={`${styles.icon} ${styles[`icon-${feature.key}`]}`}
变更后: className={styles.icon}
```

---

## FC-2: #1158 — NIM reranker missing exports

### 接口

| 属性 | 值 |
|------|-----|
| 触发条件 | `from powermem.integrations.rerank import NimRerank` |
| 涉及文件 | `src/powermem/integrations/rerank/__init__.py` |
| 变更类型 | 纯增量（新增导入和 `__all__` 条目） |

### 输入 → 输出

| | 当前状态 | 期望状态 |
|--|---------|---------|
| **输入** | `NimRerank` / `NimRerankConfig` 存在于 `nim.py` / `config/providers.py` | 同左 |
| **输出** | `ImportError` | 导入成功，类指向正确实现 |

### 错误条件

| 条件 | 处理 |
|------|------|
| `httpx` 未安装 | `nim.py` 中已有 try/except 处理，不影响导入 |
| 模块路径错误 | `.nim` 和 `.config.providers` 已验证存在 |

### 前置条件 / 后置条件

- **前置**：`src/powermem/integrations/rerank/nim.py` 和 `src/powermem/integrations/rerank/config/providers.py` 已存在
- **后置**：`from powermem.integrations.rerank import NimRerank, NimRerankConfig` 成功

### 不变量

- 现有导出列表不受影响
- `NimRerank` 和 `NimRerankConfig` 的实现不变
- `RerankFactory` 行为不变

### 精确变更

```python
# 文件: src/powermem/integrations/rerank/__init__.py

# 新增导入（第 12 行后）:
from .nim import NimRerank
from .config.providers import NimRerankConfig  # 注意：不是 .config

# __all__ 新增条目:
"RerankBase",
"RerankFactory", 
"QwenRerank",
"JinaRerank",
"GenericRerank",
"ZaiRerank",
"NimRerank",        # 新增
"BaseRerankConfig",
"QwenRerankConfig",
"JinaRerankConfig",
"ZaiRerankConfig",
"GenericRerankConfig",
"NimRerankConfig",  # 新增
```

---

## FC-3: #1151 — OceanBase forget marker lost

### 接口

| 属性 | 值 |
|------|-----|
| 触发条件 | `forget(memory_id)` 在 OceanBase 存储后端上调用 |
| 涉及文件 | `src/powermem/core/memory.py:56` (`_forget_marker_updates()`) |
| 变更类型 | 返回值结构扩展（新增 `metadata` dict） |

### 输入 → 输出

| | 当前状态 | 期望状态 |
|--|---------|---------|
| **输入** | `_forget_marker_updates()` 返回 `{should_forget: True, marked_for_forgetting_at: ISO}` | 同左，**额外**返回 `metadata` dict |
| **输出** | OceanBase 的 `_build_record_for_insert()` 静默丢弃 top-level 字段 | metadata JSON column 包含遗忘标记 |

### 精确返回值结构

```python
def _forget_marker_updates() -> Dict[str, Any]:
    timestamp = get_current_datetime().isoformat()
    return {
        # Top-level 字段（向后兼容 SQLite 等其他存储后端）
        "should_forget": True,
        "marked_for_forgetting_at": timestamp,
        # Metadata dict（OceanBase 持久化到 JSON column）
        "metadata": {
            "should_forget": True,
            "marked_for_forgetting_at": timestamp,
        },
    }
```

### 错误条件

| 条件 | 处理 |
|------|------|
| `get_current_datetime()` 返回 None | 不可能（系统函数），但 ISO 转换有 try/except |
| metadata 已有内容 | `_build_record_for_insert()` 合并 metadata，不覆盖 |

### 前置条件 / 后置条件

- **前置**：记忆已存在于存储中
- **后置**：OceanBase 的 metadata JSON column 中包含 `should_forget: true` 和 `marked_for_forgetting_at: <ISO string>`

### 不变量

- SQLite 等其他存储后端的行为不变（top-level 字段仍存在）
- `_simple_add()` 的调用方式不变
- `_build_record_for_insert()` 的其他字段映射不变
- `on_get` 触发 `delete_flag` 的链路不变

### 关键 Spec 要点

1. 返回值**同时**包含 top-level 字段和 `metadata` dict
2. `metadata` dict 的 key 为 `should_forget`（bool）和 `marked_for_forgetting_at`（ISO string）
3. `metadata` dict 被 `_build_record_for_insert()` 序列化到 OceanBase 的 metadata JSON column

---

## FC-4: #1143 — retention_score written as null

### 接口

| 属性 | 值 |
|------|-----|
| 触发条件 | `process_memory()` 持久化记忆到数据库 |
| 涉及文件 | `src/powermem/agent/implementations/multi_agent.py:352`<br>`src/powermem/agent/implementations/multi_user.py`（对应行） |
| 变更类型 | metadata 构建逻辑修复 |

### 输入 → 输出

| | 当前状态 | 期望状态 |
|--|---------|---------|
| **输入** | `memory_data.get('retention_score')` → `None`（尚未填充） | 从 `enhanced_metadata.intelligence.current_retention` 提取 |
| **输出** | 数据库 metadata 中 `retention_score: null` | `retention_score: <float>`（默认 1.0） |

### 精确变更逻辑

```python
# 文件: src/powermem/agent/implementations/multi_agent.py:349-354
# 文件: src/powermem/agent/implementations/multi_user.py（对应行）

# 变更前:
'retention_score': memory_data.get('retention_score'),

# 变更后:
'retention_score': memory_data.get('retention_score')
    or memory_data.get('enhanced_metadata', {}).get('intelligence', {}).get('current_retention')
    or 1.0,
```

### 错误条件

| 条件 | 处理 |
|------|------|
| `enhanced_metadata` 不存在 | 回退到默认值 1.0 |
| `intelligence.current_retention` 不存在 | 回退到默认值 1.0 |
| LLM 未启用（无 intelligence 数据） | 默认值 1.0 |

### 前置条件 / 后置条件

- **前置**：`_persist_memory_to_storage()` 被调用时，`memory_data` 可能尚未填充 `retention_score`
- **后置**：数据库 metadata 中 `retention_score` 为有效 float（非 null）

### 不变量

- `_persist_memory_to_storage()` 的方法签名不变
- 其他 metadata 字段的构建逻辑不变
- multi_agent.py 和 multi_user.py 都需修复

---

## FC-5: #1137 — Raw exceptions in API responses

### 接口

| 属性 | 值 |
|------|-----|
| 触发条件 | 任何 API 端点发生未处理异常 |
| 涉及文件 | 见下方完整清单 |
| 变更类型 | 错误消息模板替换 + 日志保留 |

### 涉及文件和行号完整清单

#### `src/server/utils/health_check.py`
| 行号 | 函数 | 当前 | 期望 |
|------|------|------|------|
| :131 | `_check_database_sync()` | `error_msg = str(e)` | `logger.exception(...)` + `error_msg = "Database connection failed"` |
| :224 | `_check_llm_sync()` | `error_msg = str(e)` | `logger.exception(...)` + `error_msg = "LLM health check failed"` |

#### `src/server/services/memory_service.py`
| 行号 | 当前消息 | 期望消息 |
|------|---------|---------|
| :318 | `f"Failed to ingest observation: {str(e)}"` | `"Failed to ingest observation"` |
| :368 | `"error": str(e)` | `"error": "Observation processing failed"` |
| :503 | `f"Failed to create memory: {str(e)}"` | `"Failed to create memory"` |
| :563 | `f"Failed to get memory: {str(e)}"` | `"Failed to get memory"` |
| :626 | `f"Failed to list memories: {str(e)}"` | `"Failed to list memories"` |
| :1370 | `f"Failed to update memory: {str(e)}"` | `"Failed to update memory"` |
| :1450 | `f"Failed to delete memory: {str(e)}"` | `"Failed to delete memory"` |
| :1566 | `"error": str(e)` | `"error": "Memory export failed"` |
| :1651 | `"error": str(e)` | `"error": "Memory import failed"` |
| :1762 | `f"Failed to analyze memory quality: {str(e)}"` | `"Failed to analyze memory quality"` |

#### `src/server/services/user_service.py`
| 行号 | 当前消息 | 期望消息 |
|------|---------|---------|
| :67 | `f"Failed to get user profile: {str(e)}"` | `"Failed to get user profile"` |
| :153 | `f"Failed to add user profile: {str(e)}"` | `"Failed to add user profile"` |
| :215 | `f"Failed to update user memory: {str(e)}"` | `"Failed to update user memory"` |
| :266 | `f"Failed to get user memories: {str(e)}"` | `"Failed to get user memories"` |
| :323 | `f"Failed to delete user memories: {str(e)}"` | `"Failed to delete user memories"` |
| :381 | `f"Failed to delete user profile: {str(e)}"` | `"Failed to delete user profile"` |
| :420 | `f"Failed to get profiles: {str(e)}"` | `"Failed to get profiles"` |

#### `src/server/services/agent_service.py`
| 行号 | 当前消息 | 期望消息 |
|------|---------|---------|
| :80 | `f"Failed to get agent memories: {str(e)}"` | `"Failed to get agent memories"` |
| :165 | `f"Failed to create agent memory: {str(e)}"` | `"Failed to create agent memory"` |
| :315 | `f"Failed to share memories: {str(e)}"` | `"Failed to share memories"` |

#### `src/server/services/search_service.py`
| 行号 | 当前消息 | 期望消息 |
|------|---------|---------|
| :112 | `f"Search failed: {str(e)}"` | `"Search failed"` |

### 安全分类规则

| 异常类型 | 可安全暴露？ | 原因 |
|---------|------------|------|
| `ValidationError` | ✅ 是 | 用户输入验证错误，不含内部细节 |
| `APIError` | ✅ 是 | 已由 `service_errors.py` 定义公共消息 |
| `ValueError` | ❌ 否 | 可能包含内部路径或配置 |
| `Exception` (通用) | ❌ 否 | 可能包含堆栈跟踪、连接字符串 |
| `OSError` / `IOError` | ❌ 否 | 包含文件路径 |
| `DatabaseError` | ❌ 否 | 包含 SQL 语句或连接信息 |

### 通用错误消息模板

```python
# 公共模式：保留操作描述，移除 str(e)
message = "Failed to <operation>"  # 不附加 str(e)

# 日志模式：保留完整异常信息
logger.error(f"Failed to <operation>: {e}", exc_info=True)
# 或
logger.exception(f"Failed to <operation>")
```

### 前置条件 / 后置条件

- **前置**：`service_errors.py` 中已有 `public_startup_error_message()` 模式可参考
- **后置**：所有 API 响应中 `message` 字段不包含 `str(e)` 内容

### 不变量

- `APIError` 的错误码（`ErrorCode` enum）不变
- HTTP 状态码不变
- 已有的 `APIError` raise 行为不变
- `logger.error` / `logger.exception` 的日志记录不变
- `ErrorResponse` 模型结构不变

---

## FC-6: #1141 — Six-dimension unified importance evaluation

### 接口

| 属性 | 值 |
|------|-----|
| 触发条件 | LLM 不可用时，`evaluate_importance()` 调用 `_rule_based_evaluation()` |
| 涉及文件 | `src/powermem/intelligence/importance_evaluator.py` |
| 变更类型 | `_rule_based_evaluation()` 重构 + `get_importance_breakdown()` 增强 |

### criteria_weights 默认值

```python
# importance_evaluator.py:38-45（已定义，无需修改）
criteria_weights = {
    "relevance": 0.3,
    "novelty": 0.2,
    "emotional_impact": 0.15,
    "actionable": 0.15,
    "factual": 0.1,
    "personal": 0.1,
}
# 总和 = 1.0
```

### `_rule_based_evaluation()` 返回值计算公式

```python
def _rule_based_evaluation(self, content, metadata=None, context=None):
    # 调用六个维度方法
    scores = {
        "relevance": self._evaluate_relevance(content, context),
        "novelty": self._evaluate_novelty(content, metadata),
        "emotional_impact": self._evaluate_emotional_impact(content),
        "actionable": self._evaluate_actionable(content),
        "factual": self._evaluate_factual(content),
        "personal": self._evaluate_personal(content, metadata),
    }
    
    # 加权求和
    weighted_total = sum(
        scores[dim] * self.criteria_weights[dim]
        for dim in self.criteria_weights
    )
    
    # Clamp 到 [0, 1]
    return max(0.0, min(1.0, weighted_total))
```

### `get_importance_breakdown()` 增强

```python
def get_importance_breakdown(self, content, metadata=None, context=None):
    breakdown = {}
    for criterion, weight in self.criteria_weights.items():
        # ... 现有维度计算逻辑不变 ...
        breakdown[criterion] = score
    
    # 新增：计算 weighted_total
    breakdown["weighted_total"] = sum(
        breakdown[dim] * self.criteria_weights[dim]
        for dim in self.criteria_weights
        if dim in breakdown
    )
    
    return breakdown
```

### 错误条件

| 条件 | 处理 |
|------|------|
| 某个 `_evaluate_*` 方法抛出异常 | 该维度分数设为 0.0，继续计算 |
| 所有维度分数为 0.0 | 返回 0.0（符合 AC-6.5） |
| `criteria_weights` 被配置覆盖 | 使用配置值，不使用默认值 |

### 前置条件 / 后置条件

- **前置**：`_evaluate_*` 六个维度方法已存在（:248-320）
- **后置**：`_rule_based_evaluation()` 返回值 = 加权和（clamped [0, 1]）

### 不变量

- `_evaluate_*` 六个维度方法的实现不变（除非需要增强）
- `_llm_based_evaluation()` 的逻辑不变
- `evaluate_importance()` 的方法签名和返回值范围不变
- `criteria_weights` 的默认值不变

### 关键 Spec 要点

1. `_rule_based_evaluation()` 必须调用所有六个 `_evaluate_*` 方法
2. 加权公式：`Σ(score[dim] × weight[dim])`，clamped 到 [0, 1]
3. `get_importance_breakdown()` 新增 `weighted_total` 键
4. 旧逻辑中的 metadata/context 因子（`priority`, `user_engagement`）在重构后**移除**，由维度方法统一处理

---

## FC-7: #1149 — Ebbinghaus decay in update_memory_decay

### 接口

| 属性 | 值 |
|------|-----|
| 触发条件 | `update_memory_decay()` 被调用（定时任务或手动触发） |
| 涉及文件 | `src/powermem/agent/implementations/multi_agent.py:918-979`<br>`src/powermem/agent/implementations/multi_user.py:901-962` |
| 变更类型 | 算法替换（线性 → Ebbinghaus） |

### `EbbinghausAlgorithm.calculate_current_retention()` 调用方式

```python
from powermem.intelligence import EbbinghausAlgorithm

# 初始化（从 agent manager config 获取参数）
ebbinghaus_config = self.config.get('ebbinghaus', {})
ebbinghaus = EbbinghausAlgorithm(ebbinghaus_config)

# 调用
new_retention = ebbinghaus.calculate_current_retention(memory)
```

### memory dict 结构要求

```python
# calculate_current_retention() 需要的 memory dict 结构：
memory = {
    "content": "...",
    "created_at": "2026-07-25T10:00:00",  # ISO string 或 datetime
    "metadata": {
        "intelligence": {
            "current_retention": 0.95,      # 上次计算的保留分数
            "initial_retention": 1.0,        # 初始保留分数
            "decay_rate": 1.5,               # 衰减率
            "last_reviewed": "2026-07-25T10:00:00",  # 上次复习时间
            "memory_type": "working",        # 记忆类型
            "access_count": 3,               # 访问次数
            "reinforcement_factor": 0.3,     # 强化因子
        }
    },
    # Agent memory 的顶层字段（兼容）
    "retention_score": 0.95,
    "access_count": 3,
    "last_accessed": "2026-07-25T10:00:00",
}
```

### 回退行为

```python
def update_memory_decay(self) -> Dict[str, Any]:
    try:
        ebbinghaus = self._get_ebbinghaus_algorithm()
    except Exception:
        logger.warning("EbbinghausAlgorithm not available, using fallback")
        ebbinghaus = None
    
    # ... 循环遍历记忆 ...
    for memory_id, memory_data in memories:
        if ebbinghaus:
            new_retention = ebbinghaus.calculate_current_retention(memory_data)
        else:
            # 回退：保留当前分数不变
            new_retention = memory_data.get('retention_score', 1.0)
```

### 精确变更（multi_agent.py:918-979）

```python
# 变更前（线性衰减）:
decay_rate = 0.1
time_since_access = (now - last_accessed).total_seconds() / 3600
new_score = current_score * (1 - decay_rate * time_since_access / 24)

# 变更后（Ebbinghaus）:
ebbinghaus = self._get_ebbinghaus_algorithm()
if ebbinghaus:
    new_score = ebbinghaus.calculate_current_retention(memory_data)
else:
    new_score = current_score  # 保留原值
```

### 错误条件

| 条件 | 处理 |
|------|------|
| `EbbinghausAlgorithm` 未初始化 | 回退到保留当前分数 |
| `calculate_current_retention()` 抛出异常 | 记录日志，保留当前分数 |
| memory dict 缺少 `metadata.intelligence` | `calculate_current_retention()` 内部处理（使用 created_at + initial_retention） |

### 前置条件 / 后置条件

- **前置**：`EbbinghausAlgorithm` 类已存在且已导出
- **后置**：`update_memory_decay()` 使用 Ebbinghaus 公式计算衰减

### 不变量

- `update_memory_decay()` 的方法签名不变
- 返回值结构不变：`{updated_memories, forgotten_memories, reinforced_memories}`
- 遗忘阈值判断逻辑不变（`new_score < 0.1`）
- `cleanup_forgotten_memories()` 的逻辑不变

### 关键 Spec 要点

1. `calculate_current_retention()` 的调用不需要遍历所有记忆——它是单个记忆级别的计算
2. Ebbinghaus 公式：`R = e^(-t/S)`，其中 S = `decay_rate × decay_rate_multiplier`
3. `working` 类型衰减最快（multiplier=1），`long_term` 最慢（multiplier=60）
4. `reinforcement_factor`（默认 0.3）在 `access_count > 0` 时降低衰减率

---

## NFR 映射

### NFR-1: 向后兼容

| FC | 兼容性保证 |
|----|-----------|
| FC-1 | 仅修改 CSS class 属性值，不影响 DOM 结构 |
| FC-2 | 纯增量导出，不修改已有导出 |
| FC-3 | top-level 字段仍保留，SQLite 等后端行为不变 |
| FC-4 | `retention_score` 从 null → float，下游逻辑兼容（null 检查 → float 比较） |
| FC-5 | 错误码和 HTTP 状态码不变，仅移除 `str(e)` 附加内容 |
| FC-6 | `_rule_based_evaluation()` 返回值范围 [0, 1] 不变 |
| FC-7 | 方法签名和返回值结构不变 |

### NFR-2: 最小变更

| FC | 变更范围 |
|----|---------|
| FC-1 | 1 行 JSX |
| FC-2 | ~4 行 Python（导入 + `__all__`） |
| FC-3 | ~10 行 Python（`_forget_marker_updates()` 返回值扩展） |
| FC-4 | ~6 行 Python（两个文件各 3 行） |
| FC-5 | ~30 行 Python（消息模板替换） |
| FC-6 | ~20 行 Python（`_rule_based_evaluation()` 重构 + `get_importance_breakdown()` 增强） |
| FC-7 | ~40 行 Python（两个文件各 ~20 行） |

### NFR-4: 安全（FC-5 详细规范）

- **信息泄露防护**：所有 API 响应的 `message` 字段不得包含 `str(e)` 内容
- **日志保留**：原始异常信息必须记录到服务器日志（`logger.error` / `logger.exception`）
- **Health check 特殊处理**：
  - 数据库连接失败 → `"Database connection failed"`
  - LLM 检查失败 → `"LLM health check failed"`
  - 移除 `error_msg[:197] + "..."` 截断逻辑（不再需要）
- **异常分类**：`ValidationError` 和 `APIError` 可安全暴露；其他异常类型一律使用通用消息

### NFR-5: 可观测性

| FC | 日志行为 |
|----|---------|
| FC-5 | `logger.error(f"Failed to <op>: {e}", exc_info=True)` 保留完整堆栈 |
| FC-6 | `logger.debug(f"Rule-based evaluation: {weighted_total}")` 新增调试日志 |
| FC-7 | `logger.info(f"Updated memory decay: {decay_results}")` 保留 |

---

## AC → FC 映射表

| AC | FC | 验证方式 |
|----|-----|---------|
| AC-1.1 | FC-1 | 浏览器快照：检查 class 属性 |
| AC-1.2 | FC-1 | 浏览器快照：检查图标渲染 |
| AC-1.3 | FC-1 | 响应式布局测试（≤800px） |
| AC-2.1 | FC-2 | Python `import` 测试 |
| AC-2.2 | FC-2 | Python `import` 测试 |
| AC-2.3 | FC-2 | Python `import *` 测试 |
| AC-2.4 | FC-2 | `help()` 输出检查 |
| AC-3.1 | FC-3 | OceanBase 查询 metadata JSON |
| AC-3.2 | FC-3 | OceanBase 查询 metadata JSON |
| AC-3.3 | FC-3 | SQLite 回归测试 |
| AC-3.4 | FC-3 | `get_all()` / `search()` 结果检查 |
| AC-4.1 | FC-4 | 数据库查询 metadata |
| AC-4.2 | FC-4 | 数据库查询 metadata |
| AC-4.3 | FC-4 | 单元测试：`current_retention=0.8` |
| AC-4.4 | FC-4 | 单元测试：无 `current_retention` |
| AC-5.1 | FC-5 | API 响应断言：不包含 `str(e)` |
| AC-5.2 | FC-5 | 日志断言：`logger.error` 被调用 |
| AC-5.3 | FC-5 | Health check API 响应检查 |
| AC-5.4 | FC-5 | 各服务 API 响应检查 |
| AC-5.5 | FC-5 | JSON schema 验证 |
| AC-6.1 | FC-6 | 单元测试：无 LLM 时调用六个维度 |
| AC-6.2 | FC-6 | 单元测试：加权计算验证 |
| AC-6.3 | FC-6 | `get_importance_breakdown()` 返回值检查 |
| AC-6.4 | FC-6 | 代码审查：两个引擎使用相同维度 |
| AC-6.5 | FC-6 | 单元测试：无关键词输入 |
| AC-7.1 | FC-7 | 单元测试：multi-agent 衰减 |
| AC-7.2 | FC-7 | 单元测试：multi-user 衰减 |
| AC-7.3 | FC-7 | 单元测试：working vs long_term 衰减速度 |
| AC-7.4 | FC-7 | 单元测试：access_count > 0 时衰减率 |
| AC-7.5 | FC-7 | 单元测试：EbbinghausAlgorithm 不可用 |
| AC-7.6 | FC-7 | 代码审查：算法一致性 |

---

## Gherkin 场景

### FC-1: Homepage CSS

```gherkin
Feature: Homepage feature icons render correctly

  Scenario: Feature icons use correct CSS class
    Given 访客打开首页
    When 页面加载完成
    Then 5 个 feature 图标的容器 div 的 class 属性包含 "icon"
    And 不包含 "undefined" 字面量

  Scenario: Feature icons display correctly on mobile
    Given 访客在移动端（viewport ≤ 800px）
    When 页面响应式布局生效
    Then 图标在单列布局中正确显示蓝色圆形背景
```

### FC-2: NIM reranker exports

```gherkin
Feature: NIM reranker accessible from package level

  Scenario: Direct import of NimRerank
    Given __init__.py 已更新
    When 执行 "from powermem.integrations.rerank import NimRerank"
    Then 导入成功且 NimRerank 指向正确的类

  Scenario: Direct import of NimRerankConfig
    Given __init__.py 已更新
    When 执行 "from powermem.integrations.rerank import NimRerankConfig"
    Then 导入成功且 NimRerankConfig 指向正确的配置类

  Scenario: Wildcard export includes NIM classes
    Given __init__.py 已更新
    When 执行 "from powermem.integrations.rerank import *"
    Then NimRerank 和 NimRerankConfig 均在导出列表中
```

### FC-3: OceanBase forget marker

```gherkin
Feature: Forget marker persists to OceanBase metadata

  Scenario: OceanBase stores should_forget in metadata
    Given OceanBase 存储后端
    And 记忆 ID 为 "mem_123"
    When 调用 forget("mem_123")
    Then 查询该记忆的 metadata JSON column
    And metadata 中包含 "should_forget": true

  Scenario: OceanBase stores marked_for_forgetting_at in metadata
    Given OceanBase 存储后端
    When 调用 forget(memory_id)
    Then metadata 中包含 "marked_for_forgetting_at" 字段
    And 值为有效 ISO 时间戳

  Scenario: SQLite backward compatibility
    Given SQLite 存储后端
    When 调用 forget(memory_id)
    Then should_forget 和 marked_for_forgetting_at 仍正确持久化
    And 不引入回归

  Scenario: Forgotten memory visible in search results
    Given 记忆已被标记为 should_forget
    When 执行 get_all() 或 search()
    Then 该记忆的 metadata 中包含遗忘标记
```

### FC-4: retention_score

```gherkin
Feature: retention_score correctly persisted

  Scenario: Multi-agent mode stores retention_score
    Given multi-agent 模式
    And enhanced_metadata.intelligence.current_retention = 0.8
    When process_memory() 存储一条记忆
    Then 数据库 metadata 中 retention_score = 0.8

  Scenario: Multi-user mode stores retention_score
    Given multi-user 模式
    When process_memory() 存储一条记忆
    Then 数据库 metadata 中 retention_score 不为 null

  Scenario: Default retention_score when no intelligence data
    Given intelligence 数据中无 current_retention（LLM 未启用）
    When 持久化记忆
    Then retention_score = 1.0
```

### FC-5: Security — no raw exceptions

```gherkin
Feature: API responses do not expose raw exceptions

  Scenario Outline: Service error messages are generic
    Given <service> 服务
    When <operation> 操作失败
    Then 响应中的 message 字段为 "<expected_message>"
    And 不包含原始异常的 str(e) 内容
    And 原始异常信息记录到服务器日志

    Examples:
      | service    | operation           | expected_message              |
      | memory     | create memory       | Failed to create memory       |
      | memory     | get memory          | Failed to get memory          |
      | memory     | list memories       | Failed to list memories       |
      | memory     | update memory       | Failed to update memory       |
      | memory     | delete memory       | Failed to delete memory       |
      | memory     | ingest observation  | Failed to ingest observation  |
      | user       | get user profile    | Failed to get user profile    |
      | user       | add user profile    | Failed to add user profile    |
      | agent      | get agent memories  | Failed to get agent memories  |
      | agent      | create agent memory | Failed to create agent memory |
      | search     | search              | Search failed                 |

  Scenario: Health check database error is generic
    Given health check 端点
    When 数据库连接失败
    Then error_message = "Database connection failed"
    And 不包含连接字符串或堆栈跟踪

  Scenario: Health check LLM error is generic
    Given health check 端点
    When LLM 检查失败
    Then error_message = "LLM health check failed"
```

### FC-6: Six-dimension importance evaluation

```gherkin
Feature: Rule-based evaluation uses six dimensions

  Scenario: Rule-based evaluation calls all six dimensions
    Given LLM 不可用
    When 调用 evaluate_importance("important factual data")
    Then 内部调用 _evaluate_relevance, _evaluate_novelty, _evaluate_emotional_impact, _evaluate_actionable, _evaluate_factual, _evaluate_personal
    And 返回值等于各维度分数的加权和

  Scenario: Weighted calculation with default weights
    Given criteria_weights = {"relevance": 0.3, "novelty": 0.2, ...}
    And _evaluate_relevance = 0.5, _evaluate_novelty = 0.0, 其余为 0.0
    When 调用 _rule_based_evaluation()
    Then 返回值 = 0.5 × 0.3 + 0.0 × 0.2 + ... = 0.15

  Scenario: get_importance_breakdown includes weighted_total
    Given 调用 get_importance_breakdown(content)
    When 返回 breakdown dict
    Then dict 中包含 "weighted_total" 键
    And weighted_total = Σ(score[dim] × weight[dim])

  Scenario: Empty content returns 0.0
    Given 输入内容无任何关键词匹配
    When 调用 _rule_based_evaluation()
    Then 返回值为 0.0
```

### FC-7: Ebbinghaus decay

```gherkin
Feature: update_memory_decay uses Ebbinghaus algorithm

  Scenario: Multi-agent mode uses EbbinghausAlgorithm
    Given multi-agent 模式
    And EbbinghausAlgorithm 已初始化
    When 调用 update_memory_decay()
    Then 内部使用 EbbinghausAlgorithm.calculate_current_retention(memory) 计算新保留分数

  Scenario: Multi-user mode uses EbbinghausAlgorithm
    Given multi-user 模式
    And EbbinghausAlgorithm 已初始化
    When 调用 update_memory_decay()
    Then 内部使用 EbbinghausAlgorithm.calculate_current_retention(memory) 计算新保留分数

  Scenario: Working memory decays faster than long-term
    Given 记忆类型为 "working"（衰减乘数=1）
    And 记忆类型为 "long_term"（衰减乘数=60）
    When 计算相同时间后的衰减
    Then working 记忆的保留分数低于 long_term 记忆

  Scenario: Access count reduces decay
    Given 记忆 access_count = 5
    When 计算衰减
    Then reinforcement_factor 使衰减速度降低

  Scenario: Fallback when EbbinghausAlgorithm unavailable
    Given EbbinghausAlgorithm 未初始化（配置缺失）
    When 调用 update_memory_decay()
    Then 回退到保留当前分数
    And 不抛出异常
    And 记录 warning 日志
```

---

## 测试策略

### 单元测试

| FC | 测试文件 | 关键测试用例 |
|----|---------|-------------|
| FC-3 | `tests/core/test_memory.py` | `_forget_marker_updates()` 返回值包含 `metadata` dict |
| FC-4 | `tests/agent/test_multi_agent.py` | `_persist_memory_to_storage()` 的 metadata 中 `retention_score` 非 null |
| FC-6 | `tests/intelligence/test_importance_evaluator.py` | `_rule_based_evaluation()` 加权计算；`get_importance_breakdown()` 包含 `weighted_total` |
| FC-7 | `tests/agent/test_multi_agent.py` | `update_memory_decay()` 使用 Ebbinghaus；回退行为 |

### 集成测试

| FC | 测试场景 |
|----|---------|
| FC-3 | OceanBase 存储 → forget → 查询 metadata → 验证 `should_forget` |
| FC-5 | 发送请求触发异常 → 验证响应 message 不含 `str(e)` → 验证日志包含异常 |
| FC-7 | 创建记忆 → 等待时间流逝 → 调用 `update_memory_decay()` → 验证衰减符合 Ebbinghaus 曲线 |

### API 测试

| FC | 测试场景 |
|----|---------|
| FC-2 | `GET /api/v1/rerank` 相关端点使用 NimRerank |
| FC-5 | 各端点异常响应的 JSON schema 验证 |

---

*Generated: 2026-07-25 | Phase 2 | Agent: analyst*
