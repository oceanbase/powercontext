# Architecture — powermem issue batch fix

> Phase 3 架构设计，基于 `docs/requirements.md` 和 `docs/SPEC.md` 的 7 个修复影响分析。

---

## 1. 项目架构概览

### 模块结构

```
powermem/
├── core/                    # 核心记忆管理
│   ├── memory.py            # Memory 类（同步接口，_forget_marker_updates）
│   ├── base.py              # MemoryBase 抽象
│   └── async_memory.py      # 异步接口
│
├── agent/                   # Agent 层
│   ├── agent.py             # Agent 基类
│   ├── implementations/
│   │   ├── multi_agent.py   # MultiAgentMemoryManager（_persist_memory_to_storage, update_memory_decay）
│   │   └── multi_user.py    # MultiUserMemoryManager（_persist_memory_to_storage, update_memory_decay）
│   └── abstract/            # 抽象基类和上下文
│
├── intelligence/            # 智能评估层
│   ├── manager.py           # IntelligenceManager
│   ├── intelligent_memory_manager.py  # IntelligentMemoryManager
│   ├── importance_evaluator.py  # ImportanceEvaluator（六维评估）
│   ├── ebbinghaus_algorithm.py  # EbbinghausAlgorithm（遗忘曲线）
│   ├── memory_optimizer.py  # MemoryOptimizer
│   └── plugin.py            # IntelligentMemoryPlugin, EbbinghausIntelligencePlugin
│
├── integrations/            # 外部集成
│   ├── rerank/              # Rerank 服务集成
│   │   ├── __init__.py      # 包级导出（需补充 NimRerank）
│   │   ├── nim.py           # NimRerank 类
│   │   ├── qwen.py / jina.py / zai.py / generic.py
│   │   └── config/providers.py  # NimRerankConfig
│   ├── llm/                 # LLM 集成
│   └── embeddings/          # Embedding 集成
│
├── storage/                 # 存储层
│   ├── oceanbase/
│   │   └── oceanbase.py     # OceanBase 向量存储（_build_record_for_insert）
│   ├── factory.py           # VectorStoreFactory, GraphStoreFactory
│   └── adapter.py           # StorageAdapter
│
├── configs/                 # 配置管理
└── utils/                   # 工具函数

server/                      # API 服务层
├── api/v1/                  # API 路由（memories, users, agents, search, observations, system）
├── services/                # 业务服务
│   ├── memory_service.py    # 记忆服务（10 处 str(e) 泄露）
│   ├── user_service.py      # 用户服务（7 处 str(e) 泄露）
│   ├── agent_service.py     # Agent 服务（3 处 str(e) 泄露）
│   └── search_service.py   # 搜索服务（1 处 str(e) 泄露）
├── middleware/               # 中间件（认证、限流、日志、错误处理）
├── utils/
│   ├── health_check.py      # 健康检查（2 处 str(e) 泄露）
│   └── service_errors.py    # 错误消息 helper（参考模式）
└── models/
    ├── errors.py            # APIError, ErrorResponse
    └── response.py          # 响应模型

docs/website/                # 文档网站（Docusaurus）
└── src/components/Features/
    ├── index.tsx             # Feature 组件（CSS class undefined 问题）
    └── styles.module.css     # CSS Modules
```

### 子系统关联

| 子系统 | 职责 | 本次涉及 Issue |
|--------|------|---------------|
| docs/website | 文档网站前端 | #1178 (FC-1) |
| integrations/rerank | Rerank 服务集成 | #1158 (FC-2) |
| core/memory + storage/oceanbase | 记忆存储与持久化 | #1151 (FC-3) |
| agent/implementations | Agent 记忆管理 | #1143 (FC-4), #1149 (FC-7) |
| server/services + utils | API 服务与错误处理 | #1137 (FC-5) |
| intelligence | 智能评估与衰减 | #1141 (FC-6), #1149 (FC-7) |

---

## 2. 影响分析矩阵

| Issue | FC | 模块 | 变更类型 | 影响范围 | 风险 | 优先级 |
|-------|-----|------|---------|---------|------|--------|
| #1178 | FC-1 | docs/website | 代码修复 | 仅前端组件（1 行 JSX） | 低 | P1 |
| #1158 | FC-2 | integrations/rerank | 导出补充 | 包级 API（`__init__.py`） | 低 | P1 |
| #1151 | FC-3 | core/memory + storage/oceanbase | 数据流修复 | 存储层（`_forget_marker_updates`） | 中 | P0 |
| #1143 | FC-4 | agent/implementations | 数据流修复 | Agent 层（`_persist_memory_to_storage`） | 中 | P1 |
| #1137 | FC-5 | server/services + utils | 安全修复 | API 层（21 处 `str(e)` 泄露） | 高 | P0 |
| #1141 | FC-6 | intelligence | 算法重构 | 评估引擎（`_rule_based_evaluation`） | 中 | P1 |
| #1149 | FC-7 | agent/implementations + intelligence | 算法替换 | 衰减系统（`update_memory_decay`） | 高 | P1 |

---

## 3. 各修复详细架构分析

### 3.1 FC-1: #1178 CSS class 修复

**涉及模块**：
- `docs/website/src/components/Features/index.tsx:103`
- `docs/website/src/components/Features/styles.module.css`

**依赖关系**：
- 无上游依赖（纯前端组件修复）
- 无下游影响（CSS Modules 作用域隔离）

**变更影响**：
- **变更范围**：仅 1 行 JSX 表达式
- **变更内容**：`${styles.icon} ${styles[`icon-${feature.key}`]}` → `styles.icon`
- **影响组件**：`Features` 组件的 5 个 feature 图标渲染
- **视觉影响**：无（`.icon` class 已包含完整样式）

**风险评估**：
- 风险等级：**低**
- CSS Modules 确保作用域隔离，不影响其他组件
- 无动态 CSS class 查找模式的其他使用

---

### 3.2 FC-2: #1158 NIM reranker 导出

**涉及模块**：
- `src/powermem/integrations/rerank/__init__.py`
- `src/powermem/integrations/rerank/nim.py`（已存在）
- `src/powermem/integrations/rerank/config/providers.py`（`NimRerankConfig` 已定义）

**依赖关系**：
- 上游：`nim.py` 中 `NimRerank` 类、`config/providers.py` 中 `NimRerankConfig` 类
- 下游：用户代码（`from powermem.integrations.rerank import NimRerank`）

**变更影响**：
- **变更范围**：`__init__.py` 新增 2 行导入 + 2 个 `__all__` 条目
- **变更类型**：纯增量（不修改现有导出）
- **API 影响**：新增公开 API `NimRerank` 和 `NimRerankConfig`

**风险评估**：
- 风险等级：**低**
- 纯增量变更，不破坏现有 API
- `NimRerank` 的 `httpx` 依赖在类 `__init__` 中检查，模块级导入安全

---

### 3.3 FC-3: #1151 OceanBase forget marker

**涉及模块**：
- `src/powermem/core/memory.py:56` — `_forget_marker_updates()`
- `src/powermem/core/memory.py` — `_simple_add()` 方法
- `src/powermem/storage/oceanbase/oceanbase.py:848` — `_build_record_for_insert()`

**数据流分析**：

```
当前数据流（断裂）：
forget(memory_id)
  → _forget_marker_updates()
    → {"should_forget": True, "marked_for_forgetting_at": <timestamp>}  # top-level 字段
  → storage.update_memory(updates)
    → _build_record_for_insert(vector, payload)
      → record = {
          "metadata": serialized_metadata,  # metadata dict 不含 should_forget
          "user_id": ..., "agent_id": ..., ...
        }
      → should_forget 和 marked_for_forgetting_at 被静默丢弃 ❌

修复后数据流：
forget(memory_id)
  → _forget_marker_updates()
    → {
        "should_forget": True,                    # top-level（兼容 SQLite）
        "marked_for_forgetting_at": now,          # top-level（兼容 SQLite）
        "metadata": {                             # 新增：确保进入 metadata JSON column
            "should_forget": True,
            "marked_for_forgetting_at": now,
        }
      }
  → storage.update_memory(updates)
    → _build_record_for_insert(vector, payload)
      → record["metadata"] = serialize_datetime(metadata)  # metadata dict 包含遗忘标记 ✅
```

**当前断裂点**：
- `_forget_marker_updates()` 仅返回 top-level 字段
- OceanBase 的 `_build_record_for_insert()` 只映射已知 DB 列，top-level 的 `should_forget` 和 `marked_for_forgetting_at` 不在映射列表中
- 这些字段未被合并到 `metadata` dict，因此不进入 metadata JSON column

**修复方案**：
- 在 `_forget_marker_updates()` 返回值中同时包含 `metadata` 内嵌字段
- OceanBase 的 `_build_record_for_insert()` 会将 `metadata` dict 序列化为 JSON 写入 `metadata` 列
- 保留 top-level 字段以兼容 SQLite 等其他存储后端

**风险评估**：
- 风险等级：**中**
- 需验证 `on_get` 触发 `delete_flag` 时 `updates.update(_forget_marker_updates())` 不会覆盖已有 metadata
- 需验证 `search()` 和 `get_all()` 返回的记忆 metadata 中包含遗忘标记

---

### 3.4 FC-4: #1143 retention_score 修复

**涉及模块**：
- `src/powermem/agent/implementations/multi_agent.py` — `_persist_memory_to_storage()`
- `src/powermem/agent/implementations/multi_user.py` — `_persist_memory_to_storage()`

**数据流分析**：

```
当前数据流（问题）：
process_memory()
  → enhanced_metadata = intelligent_manager.process_metadata()  # 包含 intelligence.current_retention
  → temp_memory_data = {
      'content': ...,
      'metadata': enhanced_metadata,  # 包含 intelligence.current_retention
      'retention_score': enhanced_metadata.get('intelligence', {}).get('current_retention', 1.0),  # ✅ 正确赋值
    }
  → _persist_memory_to_storage(temp_memory_data)
    → metadata={
        'retention_score': memory_data.get('retention_score'),  # ❌ 可能为 None
        # ... 其他字段
        **memory_data.get('metadata', {})  # 展开可能覆盖 retention_score
      }

问题分析：
- multi_agent.py:252 正确赋值了 retention_score，但 _persist_memory_to_storage:352 使用 memory_data.get('retention_score')
- 如果 _persist_memory_to_storage 在 memory_data 完整构建之前被调用，retention_score 为 None
- **memory_data.get('metadata', {}) 展开时，如果 enhanced_metadata 中有顶层 retention_score key，会覆盖前面的值**

修复方案：
- 直接从 memory_data['metadata']（即 enhanced_metadata）中提取 intelligence.current_retention
- 使用 memory_data.get('metadata', {}).get('intelligence', {}).get('current_retention', 1.0)

multi_agent.py 和 multi_user.py 差异：
- multi_agent.py:352 — `'retention_score': memory_data.get('retention_score')`
- multi_user.py:270 — `'retention_score': memory_data.get('retention_score')`（相同模式）
- multi_user.py 额外有 `privacy_level` 和 `shared_with` 字段
```

**风险评估**：
- 风险等级：**中**
- 需确保 `**memory_data.get('metadata', {})` 展开不覆盖修复后的 `retention_score`
- 需验证 LLM 未启用时的默认值行为（1.0）

---

### 3.5 FC-5: #1137 API 异常信息泄露

**涉及模块**：
- `src/server/services/memory_service.py` — 10 处 `str(e)` 泄露
- `src/server/services/user_service.py` — 7 处 `str(e)` 泄露
- `src/server/services/agent_service.py` — 3 处 `str(e)` 泄露
- `src/server/services/search_service.py` — 1 处 `str(e)` 泄露
- `src/server/utils/health_check.py` — 2 处 `str(e)` 泄露
- `src/server/utils/service_errors.py` — 参考模式

**错误处理架构**：

```
当前模式（不安全）：
try:
    result = await service_operation()
except Exception as e:
    logger.error(f"Failed: {e}", exc_info=True)  # 日志记录 ✅
    raise APIError(
        code=ErrorCode.XXX,
        message=f"Failed: {str(e)}",  # ❌ 泄露内部信息
        status_code=500,
    )

目标模式（安全）：
try:
    result = await service_operation()
except Exception as e:
    logger.error(f"Failed: {e}", exc_info=True)  # 日志记录 ✅
    raise APIError(
        code=ErrorCode.XXX,
        message="Failed to XXX",  # ✅ 通用消息
        status_code=500,
    )
```

**`service_errors.py` 的角色**：
- 已有 `public_startup_error_message()` 和 `public_startup_error_with_recommendation()` helper
- 提供了通用错误消息的参考模式
- 本次修复需在各 service 中统一采用类似模式

**需要修改的文件清单**：

| 文件 | 泄露点数 | 修复模式 |
|------|---------|---------|
| memory_service.py | 10 | 移除 `str(e)`，使用通用消息 |
| user_service.py | 7 | 移除 `str(e)`，使用通用消息 |
| agent_service.py | 3 | 移除 `str(e)`，使用通用消息 |
| search_service.py | 1 | 移除 `str(e)`，使用通用消息 |
| health_check.py | 2 | 替换为固定错误消息 |

**风险评估**：
- 风险等级：**高**
- 需逐一排查所有 `str(e)` 位置，区分安全可暴露的（如 `ValidationError`）和敏感的
- 需确保不破坏已有的 `APIError` 错误处理流程
- 需确保原始异常仍记录到日志（`logger.error(..., exc_info=True)`）

---

### 3.6 FC-6: #1141 重要性评估统一

**涉及模块**：
- `src/powermem/intelligence/importance_evaluator.py`

**`ImportanceEvaluator` 类结构**：

```python
class ImportanceEvaluator:
    def __init__(self, config, llm_config):
        self.criteria_weights = {
            "relevance": 0.3,
            "novelty": 0.2,
            "emotional_impact": 0.15,
            "actionable": 0.15,
            "factual": 0.1,
            "personal": 0.1,
        }

    def evaluate_importance(content, metadata, context) -> float:
        if self.llm:
            return self._llm_based_evaluation(...)
        else:
            return self._rule_based_evaluation(...)

    def _rule_based_evaluation(content, metadata, context) -> float:
        # ❌ 当前：硬编码关键词匹配 + 简单加分
        # ✅ 目标：调用六个 _evaluate_* 方法 + 加权计算

    def _llm_based_evaluation(content, metadata, context) -> float:
        # LLM 评估，失败时回退到 _rule_based_evaluation

    def get_importance_breakdown(content, metadata, context) -> Dict:
        # ❌ 当前：返回各维度分数但无 weighted_total
        # ✅ 目标：返回各维度分数 + weighted_total

    def _evaluate_relevance(content, context) -> float: ...
    def _evaluate_novelty(content, metadata) -> float: ...
    def _evaluate_emotional_impact(content) -> float: ...
    def _evaluate_actionable(content) -> float: ...
    def _evaluate_factual(content) -> float: ...
    def _evaluate_personal(content, metadata) -> float: ...
```

**六个 `_evaluate_*` 方法的调用关系**：
- `_evaluate_relevance(content, context)` — 关键词匹配（relevant, related, connected, associated）
- `_evaluate_novelty(content, metadata)` — 关键词匹配（new, first, unique, novel, discovered）
- `_evaluate_emotional_impact(content)` — 关键词匹配（love, hate, happy, sad, angry, excited, afraid）
- `_evaluate_actionable(content)` — 关键词匹配（todo, task, deadline, meeting, call, email）
- `_evaluate_factual(content)` — 关键词匹配（data, statistics, research, study, evidence）
- `_evaluate_personal(content, metadata)` — 关键词匹配（my, i, me, prefer, favorite, birthday）

**`_rule_based_evaluation()` vs `_llm_based_evaluation()` 的对齐**：
- `_llm_based_evaluation()` 通过 LLM 返回六维分数 + 加权计算
- `_rule_based_evaluation()` 应使用相同的六维框架 + 相同的 `criteria_weights`
- 两者返回值范围均为 [0, 1]

**风险评估**：
- 风险等级：**中**
- 现有 `_evaluate_*` 方法使用简单关键词匹配，分数可能偏低
- 重构后 `_rule_based_evaluation()` 的返回值可能与旧逻辑不同（但范围仍在 [0, 1]）
- `_llm_based_evaluation()` 的 fallback 逻辑不变

---

### 3.7 FC-7: #1149 Ebbinghaus 衰减算法

**涉及模块**：
- `src/powermem/agent/implementations/multi_agent.py` — `update_memory_decay()` (~L918-951)
- `src/powermem/agent/implementations/multi_user.py` — `update_memory_decay()` (~L901-934)
- `src/powermem/intelligence/ebbinghaus_algorithm.py` — `EbbinghausAlgorithm` 类
- `src/powermem/intelligence/__init__.py` — `EbbinghausAlgorithm` 已导出

**`EbbinghausAlgorithm` 类接口**：

```python
class EbbinghausAlgorithm:
    def __init__(self, config: Dict[str, Any]):
        self.initial_retention = config.get("initial_retention", 1.0)
        self.decay_rate = config.get("decay_rate", 1.5)
        self.decay_rate_multipliers = {"working": 1, "short_term": 7, "long_term": 60}
        self.reinforcement_factor = config.get("reinforcement_factor", 0.3)
        self.working_threshold = config.get("working_threshold", 0.3)

    def calculate_current_retention(memory: Dict) -> float:
        # R = stored_retention * e^(-t/S)
        # S = decay_rate * memory_type_multiplier * (1 + reinforcement_factor * ln(1 + access_count))

    def should_forget(memory: Dict) -> bool:
        return calculate_current_retention(memory) < working_threshold

    def calculate_decay(created_at, decay_rate) -> float:
        # e^(-t/S)

    def reinforce(memory: Dict) -> Dict:
        # 强化记忆，提升 current_retention
```

**`update_memory_decay()` 当前实现 vs 目标实现**：

```
当前实现（线性衰减）：
decay_rate = 0.1
time_since_access = (now - last_accessed).total_seconds() / 3600
new_score = current_score * (1 - decay_rate * time_since_access / 24)
new_score = max(0.0, min(1.0, new_score))

问题：
1. 线性衰减可能产生负值（虽有 clamp）
2. 不使用记忆类型乘数（working vs long_term）
3. 不使用强化因子（access_count）
4. 与 IntelligentMemoryManager 中的衰减不一致

目标实现（Ebbinghaus 衰减）：
if not hasattr(self, '_ebbinghaus'):
    intelligent_config = self._get_intelligent_memory_config()
    self._ebbinghaus = EbbinghausAlgorithm(intelligent_config)

new_score = self._ebbinghaus.calculate_current_retention(memory_data)
forgotten = self._ebbinghaus.should_forget(memory_data)
```

**multi_agent.py 和 multi_user.py 的公共化可能性**：
- 两者的 `update_memory_decay()` 实现几乎相同（遍历记忆、计算衰减、更新分数）
- 差异仅在记忆存储结构：`self.scope_memories[scope][memory_type]` vs `self.user_memories[user_id][memory_type]`
- 可考虑抽取公共方法 `_update_decay_for_memories(memories_dict)`，但本次修复范围不包括重构
- 建议：在本次修复中保持两个文件独立修改，后续版本再考虑公共化

**风险评估**：
- 风险等级：**高**
- `EbbinghausAlgorithm` 需要 memory dict 包含 `metadata.intelligence` 结构
- 需确保 agent memory 的数据格式兼容
- 需保留 `forgotten_memories` 和 `reinforced_memories` 统计
- 需处理 `EbbinghausAlgorithm` 未初始化的情况（回退到默认配置）

---

## 4. 依赖关系图

### 修复之间的依赖关系

```
FC-1 (CSS) ──────────────────────────────────────── 独立，无依赖
FC-2 (NIM 导出) ─────────────────────────────────── 独立，无依赖
FC-3 (OceanBase forget) ─────────────────────────── 独立，无依赖
FC-4 (retention_score) ──────────────────────────── 独立，但与 FC-7 共享数据模型
FC-5 (API 安全) ─────────────────────────────────── 独立，无依赖
FC-6 (重要性评估) ───────────────────────────────── 独立，但 FC-7 依赖其输出
FC-7 (Ebbinghaus 衰减) ─────────────────────────── 依赖 FC-6（评估输出 → 衰减输入）

依赖链：
FC-6 → FC-7（重要性评估输出作为衰减计算的输入）
FC-4 ← → FC-7（共享 retention_score 数据模型，但修复范围不重叠）
```

### 推荐的实施顺序

```
批次 1（独立，可并行）：
  FC-1 (CSS) ── 风险低，1 行变更
  FC-2 (NIM 导出) ── 风险低，纯增量
  FC-3 (OceanBase forget) ── 风险中，数据流修复
  FC-5 (API 安全) ── 风险高，但变更模式统一

批次 2（有依赖关系）：
  FC-6 (重要性评估) ── 需在 FC-7 之前完成
  FC-4 (retention_score) ── 与 FC-7 共享数据模型，建议在 FC-7 之前完成

批次 3（依赖批次 2）：
  FC-7 (Ebbinghaus 衰减) ── 依赖 FC-6 的评估框架，FC-4 的数据模型
```

**验证顺序**：
1. FC-1, FC-2, FC-3, FC-5 可独立验证
2. FC-6 验证后，FC-7 才能正确验证
3. FC-4 验证确保 retention_score 非 null，FC-7 验证确保衰减算法正确

---

## 5. 技术选型依据

### FC-1: CSS class 修复

| 方案 | 优势 | 劣势 | 选择 |
|------|------|------|------|
| A: 移除动态 class 查找 | 最小变更，1 行修改 | 无 | ✅ 选择 |
| B: 在 CSS 中添加 `.icon-developer` 等 class | 保留动态查找 | 5 个新 class，冗余 | ❌ |

**理由**：`.icon` class 已包含完整样式，动态查找是 PR #1170 遗留的死代码。

### FC-2: NIM reranker 导出

| 方案 | 优势 | 劣势 | 选择 |
|------|------|------|------|
| A: 在 `__init__.py` 新增导入 | 标准 Python 包模式 | 无 | ✅ 选择 |
| B: 用户直接导入子模块 | 不修改包结构 | 用户体验差 | ❌ |

**理由**：遵循现有 rerank 包的导出模式（QwenRerank, JinaRerank 等均已导出）。

### FC-3: OceanBase forget marker

| 方案 | 优势 | 劣势 | 选择 |
|------|------|------|------|
| A: 在 `_forget_marker_updates()` 中同时写入 metadata | 最小变更，兼容所有后端 | metadata 字段重复 | ✅ 选择 |
| B: 修改 `_build_record_for_insert()` 映射 | 统一处理 | 影响所有写入操作 | ❌ |
| C: 修改 `_simple_add()` 合并逻辑 | 集中处理 | 需理解复杂的数据流 | ❌ |

**理由**：方案 A 最小化变更范围，同时兼容 SQLite 和 OceanBase。

### FC-4: retention_score 修复

| 方案 | 优势 | 劣势 | 选择 |
|------|------|------|------|
| A: 直接从 enhanced_metadata 提取 | 明确数据来源 | 需处理嵌套 dict | ✅ 选择 |
| B: 在 _persist_memory_to_storage 之前赋值 | 简单 | 需修改调用方 | ❌ |

**理由**：方案 A 直接从数据源提取，避免时序依赖问题。

### FC-5: API 异常信息泄露

| 方案 | 优势 | 劣势 | 选择 |
|------|------|------|------|
| A: 逐处移除 str(e) | 精确控制 | 工作量大 | ✅ 选择 |
| B: 全局错误处理中间件 | 集中处理 | 可能遗漏特定场景 | ❌ 辅助 |

**理由**：方案 A 确保每个泄露点都被正确处理，同时保留 `service_errors.py` 的 helper 模式作为参考。

### FC-6: 重要性评估统一

| 方案 | 优势 | 劣势 | 选择 |
|------|------|------|------|
| A: 重用现有 _evaluate_* 方法 | 代码复用，一致性 | 分数可能偏低 | ✅ 选择 |
| B: 重写 _evaluate_* 方法 | 更精确 | 工作量大，风险高 | ❌ |

**理由**：方案 A 利用现有基础设施，确保规则引擎和 LLM 引擎使用相同框架。

### FC-7: Ebbinghaus 衰减算法

| 方案 | 优势 | 劣势 | 选择 |
|------|------|------|------|
| A: 使用现有 EbbinghausAlgorithm | 算法一致，可配置 | 需适配数据格式 | ✅ 选择 |
| B: 实现新的衰减函数 | 灵活 | 重复代码，不一致 | ❌ |

**理由**：项目已有完整的 `EbbinghausAlgorithm` 实现，重用避免重复造轮子。

---

## 6. NFR 架构支持

### 向后兼容性保障

| FC | 保障措施 |
|----|---------|
| FC-1 | 视觉效果不变，`.icon` class 已包含完整样式 |
| FC-2 | 纯增量导出，不修改现有 API |
| FC-3 | 保留 top-level 字段兼容 SQLite，新增 metadata 内嵌字段 |
| FC-4 | `retention_score` 字段已存在，仅改变值来源 |
| FC-5 | `APIError` 和 `DependencyStatus` 结构不变，仅 message 内容变化 |
| FC-6 | `_rule_based_evaluation()` 返回值范围保持 [0, 1] |
| FC-7 | `update_memory_decay()` 方法签名和返回值结构不变 |

### 安全性保障

| FC | 保障措施 |
|----|---------|
| FC-5 | 所有 API 响应不暴露数据库连接字符串、内部路径、堆栈跟踪 |
| FC-5 | 原始异常信息仍通过 `logger.error(..., exc_info=True)` 记录到日志 |
| FC-5 | 区分安全可暴露的错误（如 `ValidationError`）和敏感的内部异常 |

### 可观测性保障

| FC | 保障措施 |
|----|---------|
| FC-3 | 日志中 `Submitted N forget marker update operations` 保持不变 |
| FC-5 | 原始异常信息仍记录到服务器日志 |
| FC-7 | `update_memory_decay()` 的日志输出保持不变（`Updated memory decay: {...}`） |

---

## 附录：变更文件清单

| FC | 文件 | 变更行数（估计） |
|----|------|----------------|
| FC-1 | `docs/website/src/components/Features/index.tsx` | ~1 |
| FC-2 | `src/powermem/integrations/rerank/__init__.py` | ~4 |
| FC-3 | `src/powermem/core/memory.py` | ~5 |
| FC-4 | `src/powermem/agent/implementations/multi_agent.py` | ~3 |
| FC-4 | `src/powermem/agent/implementations/multi_user.py` | ~3 |
| FC-5 | `src/server/services/memory_service.py` | ~20 |
| FC-5 | `src/server/services/user_service.py` | ~14 |
| FC-5 | `src/server/services/agent_service.py` | ~6 |
| FC-5 | `src/server/services/search_service.py` | ~2 |
| FC-5 | `src/server/utils/health_check.py` | ~4 |
| FC-6 | `src/powermem/intelligence/importance_evaluator.py` | ~30 |
| FC-7 | `src/powermem/agent/implementations/multi_agent.py` | ~20 |
| FC-7 | `src/powermem/agent/implementations/multi_user.py` | ~20 |
| **总计** | **13 文件** | **~132 行** |
