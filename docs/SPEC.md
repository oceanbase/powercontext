# SPEC — powermem issue batch fix

> Phase 2 行为契约 Spec，基于 `docs/requirements.md` 的 7 个用户故事。
> 每个 FC 定义函数契约、行为变更、NFR 和边界条件。

---

## FC-1: Issue #1178 — 修复 CSS class undefined

### 函数契约

- **文件**: `docs/website/src/components/Features/index.tsx:103`
- **函数**: JSX 模板渲染（`Features` 组件内 `.map()` 循环）
- **当前代码**:
  ```tsx
  <div className={`${styles.icon} ${styles[`icon-${feature.key}`]}`}>
  ```
- **前置条件**: `styles` 是 CSS Modules 导出的对象，仅包含 `styles.module.css` 中定义的 class
- **后置条件**: 图标容器 div 的 className 仅包含已定义的 CSS class，不出现 `undefined`
- **错误条件**: 当 `styles[key]` 未定义时，CSS Modules 返回 `undefined`，模板字面量产生 `"icon undefined"` 字符串

### 行为变更

- **修复前**: `styles[`icon-${feature.key}`]` 对 `developer`、`intelligent`、`multiAgent`、`multimodal`、`storage` 五个 key 均返回 `undefined`，因为 CSS 中仅定义了 `.icon` class，无 `.icon-developer` 等独立 class。浏览器渲染时 div 的 className 为 `"icon undefined"`。
- **修复后**: 移除动态 class 查找，仅使用 `styles.icon`：
  ```tsx
  <div className={styles.icon}>
  ```
  CSS 中 `.icon` 已包含完整的蓝色圆形背景样式，无需 feature-specific class。

### NFR

- **NFR-1.1 向后兼容**: 视觉效果不变，`.icon` class 已包含所有样式属性（`display: grid`, `place-items: center`, `border-radius: 9px`, `background: color-mix(...)`, `color: #0083ff`）
- **NFR-1.2 最小变更**: 仅修改一行 JSX 表达式
- **NFR-1.3 响应式**: `@media (max-width: 800px)` 和 `@media (max-width: 480px)` 中 `.card` 的样式不受影响

### 边界条件

- CSS Modules 的作用域隔离确保 `.icon` 不与其他组件冲突
- 无其他组件使用类似的 `styles[`icon-${key}`]` 动态查找模式
- 5 个 feature key（`developer`, `intelligent`, `multiAgent`, `multimodal`, `storage`）的 class 引用全部被移除

---

## FC-2: Issue #1158 — NIM reranker 导出

### 函数契约

- **文件**: `src/powermem/integrations/rerank/__init__.py`
- **函数**: 模块级导入和 `__all__` 导出列表
- **输入**: 无（模块导入时自动执行）
- **输出**: `__all__` 列表包含 `NimRerank` 和 `NimRerankConfig`
- **前置条件**: `nim.py` 中 `NimRerank` 类和 `config/providers.py` 中 `NimRerankConfig` 类已存在
- **后置条件**: `from powermem.integrations.rerank import NimRerank` 成功
- **错误条件**: 若 `httpx` 未安装，`NimRerank.__init__()` 抛出 `ImportError`（已有 try/except 处理，不影响模块级导入）

### 行为变更

- **修复前**: `__init__.py` 导入了 `QwenRerank`, `JinaRerank`, `GenericRerank`, `ZaiRerank` 及其 Config，但未导入 `NimRerank` 和 `NimRerankConfig`。`__all__` 列表中无 NIM 相关条目。
- **修复后**: 在 `__init__.py` 中新增：
  ```python
  from .nim import NimRerank
  from .config.providers import NimRerankConfig
  ```
  并在 `__all__` 中添加 `"NimRerank"` 和 `"NimRerankConfig"`。

### NFR

- **NFR-2.1 向后兼容**: 纯增量变更，不修改任何现有导出
- **NFR-2.2 依赖安全**: `NimRerank` 的 `httpx` 依赖在类 `__init__` 中检查（`try: import httpx except ImportError`），模块级导入不会触发依赖错误
- **NFR-2.3 代码质量**: 导入顺序遵循现有模式（先 base/factory，再 providers，再 configs）

### 边界条件

- `NimRerankConfig` 定义在 `.config.providers` 子模块中（不是 `.config`），导入路径为 `.config.providers`
- `from powermem.integrations.rerank import *` 应导出 `NimRerank` 和 `NimRerankConfig`
- `help(powermem.integrations.rerank)` 应显示新增的两个类

---

## FC-3: Issue #1151 — OceanBase forget marker

### 函数契约

- **文件**: `src/powermem/core/memory.py:56`
- **函数**: `_forget_marker_updates() -> Dict[str, Any]`
- **输入**: 无
- **输出**: `Dict` 包含 `should_forget: True`, `marked_for_forgetting_at: <ISO timestamp>`, 以及新增的 `metadata` dict
- **前置条件**: `get_current_datetime()` 返回有效 datetime
- **后置条件**: 返回值同时包含 top-level 字段和 metadata 内嵌字段
- **错误条件**: 无（纯数据构造函数）

### 行为变更

- **修复前**:
  ```python
  def _forget_marker_updates() -> Dict[str, Any]:
      return {
          "should_forget": True,
          "marked_for_forgetting_at": get_current_datetime().isoformat(),
      }
  ```
  返回的 dict 作为 `storage.update_memory()` 的 `updates` 参数传递。在 OceanBase 的 `_build_record_for_insert()` 中，只映射已知 DB 列（`user_id`, `agent_id`, `hash`, `category` 等），top-level 的 `should_forget` 和 `marked_for_forgetting_at` 被静默丢弃，不进入 metadata JSON column。

- **修复后**:
  ```python
  def _forget_marker_updates() -> Dict[str, Any]:
      now = get_current_datetime().isoformat()
      return {
          "should_forget": True,
          "marked_for_forgetting_at": now,
          "metadata": {
              "should_forget": True,
              "marked_for_forgetting_at": now,
          },
      }
  ```
  OceanBase 的 `_build_record_for_insert()` 会将 `metadata` dict 序列化为 JSON 写入 `metadata` 列。同时保留 top-level 字段以兼容 SQLite 等其他存储后端。

### NFR

- **NFR-3.1 向后兼容**: SQLite 等非 OceanBase 后端通过 `storage.update_memory()` 接收 dict，已有逻辑处理 top-level 字段；新增 `metadata` 键不破坏现有行为
- **NFR-3.2 最小变更**: 仅修改 `_forget_marker_updates()` 函数
- **NFR-3.3 数据完整性**: 确保 OceanBase metadata JSON column 中包含 `should_forget` 和 `marked_for_forgetting_at`
- **NFR-3.4 可观测性**: 日志中 `Submitted N forget marker update operations` 保持不变

### 边界条件

- `_build_record_for_insert()` 使用 `serialize_datetime(metadata)` 处理 datetime 对象；`marked_for_forgetting_at` 已是 ISO 字符串，无需额外处理
- `on_get` 触发 `delete_flag` 时调用 `_forget_marker_updates()` 并合并到 `updates` dict（`updates.update(_forget_marker_updates())`），新增 `metadata` 键会覆盖已有 `metadata`（如有）
- 需验证 `search()` 和 `get_all()` 返回的记忆 metadata 中包含遗忘标记

---

## FC-4: Issue #1143 — retention_score null 修复

### 函数契约

- **文件**:
  - `src/powermem/agent/implementations/multi_agent.py:328` — `_persist_memory_to_storage()`
  - `src/powermem/agent/implementations/multi_user.py:249` — `_persist_memory_to_storage()`
- **函数**: `_persist_memory_to_storage(self, memory_data: Dict[str, Any]) -> int`
- **输入**: `memory_data` dict，包含 `content`, `agent_id`, `scope`, `memory_type`, `metadata`（含 `enhanced_metadata`）
- **输出**: Snowflake ID (int)
- **前置条件**: `memory_data['metadata']` 包含 `intelligence.current_retention` 字段
- **后置条件**: 传递给 `Memory.add()` 的 metadata dict 中 `retention_score` 不为 null
- **错误条件**: 若 `intelligence.current_retention` 缺失，使用默认值 1.0

### 行为变更

- **修复前** (multi_agent.py):
  ```python
  metadata={
      'scope': ...,
      'memory_type': ...,
      'retention_score': memory_data.get('retention_score'),  # None — memory_data 此时无此字段
      'importance_level': memory_data.get('importance_level'),
      **memory_data.get('metadata', {})
  }
  ```
  `memory_data` 在 `_persist_memory_to_storage()` 调用时尚未填充 `retention_score`（该值在后续从 `enhanced_metadata.intelligence.current_retention` 提取），导致 `retention_score` 为 null。

- **修复后** (multi_agent.py):
  ```python
  metadata={
      'scope': ...,
      'memory_type': ...,
      'retention_score': memory_data.get('metadata', {}).get('intelligence', {}).get('current_retention', 1.0),
      'importance_level': memory_data.get('metadata', {}).get('intelligence', {}).get('importance_score'),
      **memory_data.get('metadata', {})
  }
  ```
  直接从 `memory_data['metadata']`（即 `enhanced_metadata`）中提取 `intelligence.current_retention`。

- **修复后** (multi_user.py): 同样的模式，从 `memory_data.get('metadata', {}).get('intelligence', {}).get('current_retention', 1.0)` 提取。

### NFR

- **NFR-4.1 向后兼容**: `retention_score` 字段已存在于 metadata 结构中，修复仅改变其值来源
- **NFR-4.2 最小变更**: 仅修改 `_persist_memory_to_storage()` 中 metadata dict 的构造逻辑
- **NFR-4.3 数据质量**: 确保数据库中 `retention_score` 始终为有效浮点数（非 null）
- **NFR-4.4 默认值**: 当 `intelligence` 或 `current_retention` 缺失时（如 LLM 未启用），默认值为 1.0

### 边界条件

- `enhanced_metadata` 由 `intelligent_manager.process_metadata()` 返回，其中 `intelligence.current_retention` 由 `EbbinghausAlgorithm` 计算
- 当 LLM 未启用时，`enhanced_metadata` 可能不含 `intelligence` 字段，此时回退到默认值 1.0
- `**memory_data.get('metadata', {})` 展开可能覆盖前面的 `retention_score`——需确认 `enhanced_metadata` 中不包含顶层 `retention_score` key（当前实现中不包含）
- multi_agent.py 和 multi_user.py 的 `_persist_memory_to_storage()` 实现类似但不完全相同（multi_user.py 多了 `privacy_level` 和 `shared_with`），需分别修复

---

## FC-5: Issue #1137 — API 异常信息泄露修复

### 函数契约

- **涉及文件**:
  - `src/server/utils/health_check.py:131,224` — `_check_database_sync()`, `_check_llm_sync()`
  - `src/server/services/memory_service.py` — 10 处 `str(e)` 泄露
  - `src/server/services/user_service.py` — 7 处 `str(e)` 泄露
  - `src/server/services/agent_service.py` — 3 处 `str(e)` 泄露
  - `src/server/services/search_service.py` — 1 处 `str(e)` 泄露
  - `src/server/utils/service_errors.py` — 参考模式
- **函数**: 各 service 方法的 `except` 块和 health check 的异常处理
- **输入**: 原始异常 `e`
- **输出**: `APIError` 或 `DependencyStatus` 对象，`message`/`error_message` 字段为通用消息
- **前置条件**: 异常已发生
- **后置条件**: 响应中不包含 `str(e)` 原始内容；原始异常仅记录到日志
- **错误条件**: 无新错误引入

### 行为变更

- **修复前** (以 memory_service.py 为例):
  ```python
  except Exception as e:
      logger.error(f"Failed to create memory: {e}", exc_info=True)
      raise APIError(
          code=ErrorCode.MEMORY_CREATE_FAILED,
          message=f"Failed to create memory: {str(e)}",  # 泄露内部信息
          status_code=500,
      )
  ```

- **修复后**:
  ```python
  except Exception as e:
      logger.error(f"Failed to create memory: {e}", exc_info=True)
      raise APIError(
          code=ErrorCode.MEMORY_CREATE_FAILED,
          message="Failed to create memory",  # 通用消息，不含 str(e)
          status_code=500,
      )
  ```

- **health_check.py 修复**:
  ```python
  # 修复前
  error_msg = str(e)
  if len(error_msg) > 200:
      error_msg = error_msg[:197] + "..."
  return DependencyStatus(..., error_message=error_msg, ...)

  # 修复后
  logger.error(f"Database health check failed: {e}", exc_info=True)
  return DependencyStatus(..., error_message="Database connection failed", ...)
  ```

### 通用消息映射

| 位置 | 原始泄露 | 修复后通用消息 |
|------|---------|--------------|
| memory_service:318 | `f"Failed to ingest observation: {str(e)}"` | `"Failed to ingest observation"` |
| memory_service:368 | `"error": str(e)` | `"error": "Internal server error"` |
| memory_service:503 | `f"Failed to create memory: {str(e)}"` | `"Failed to create memory"` |
| memory_service:563 | `f"Failed to get memory: {str(e)}"` | `"Failed to get memory"` |
| memory_service:626 | `f"Failed to list memories: {str(e)}"` | `"Failed to list memories"` |
| memory_service:1370 | `f"Failed to update memory: {str(e)}"` | `"Failed to update memory"` |
| memory_service:1450 | `f"Failed to delete memory: {str(e)}"` | `"Failed to delete memory"` |
| memory_service:1566 | `"error": str(e)` | `"error": "Internal server error"` |
| memory_service:1651 | `"error": str(e)` | `"error": "Internal server error"` |
| memory_service:1762 | `f"Failed to analyze memory quality: {str(e)}"` | `"Failed to analyze memory quality"` |
| user_service:67 | `f"Failed to get user profile: {str(e)}"` | `"Failed to get user profile"` |
| user_service:153 | `f"Failed to add user profile: {str(e)}"` | `"Failed to add user profile"` |
| user_service:215 | `f"Failed to update user memory: {str(e)}"` | `"Failed to update user memory"` |
| user_service:266 | `f"Failed to get user memories: {str(e)}"` | `"Failed to get user memories"` |
| user_service:323 | `f"Failed to delete user memories: {str(e)}"` | `"Failed to delete user memories"` |
| user_service:381 | `f"Failed to delete user profile: {str(e)}"` | `"Failed to delete user profile"` |
| user_service:420 | `f"Failed to get profiles: {str(e)}"` | `"Failed to get profiles"` |
| agent_service:80 | `f"Failed to get agent memories: {str(e)}"` | `"Failed to get agent memories"` |
| agent_service:165 | `f"Failed to create agent memory: {str(e)}"` | `"Failed to create agent memory"` |
| agent_service:315 | `f"Failed to share memories: {str(e)}"` | `"Failed to share memories"` |
| search_service:112 | `f"Search failed: {str(e)}"` | `"Search failed"` |
| health_check:131 | `str(e)` (截断到 200 字符) | `"Database connection failed"` |
| health_check:224 | `str(e)` (截断到 200 字符) | `"LLM service check failed"` |

### NFR

- **NFR-5.1 安全**: 所有 API 响应不暴露数据库连接字符串、内部路径、堆栈跟踪等敏感信息
- **NFR-5.2 可观测性**: 原始异常信息仍通过 `logger.error(..., exc_info=True)` 记录到服务器日志
- **NFR-5.3 向后兼容**: `APIError` 和 `DependencyStatus` 的结构不变，仅 `message`/`error_message` 内容变化
- **NFR-5.4 错误响应格式**: 响应结构符合 `ErrorResponse` 模型（`error.code`, `error.message`, `error.details`）

### 边界条件

- `service_errors.py` 中的 `public_startup_error_message()` 模式保持不变，可作为参考
- `batch_ingest_observations()` 中的 per-item 错误（memory_service.py:368）也需修复
- 需区分安全可暴露的错误（如 `ValidationError`）和敏感的内部异常——`ValidationError` 的 `e.errors()` 是安全的
- health_check.py 中 `DependencyStatus` 的 `error_message` 字段用于前端展示，不应包含内部细节

---

## FC-6: Issue #1141 — 重要性评估统一

### 函数契约

- **文件**: `src/powermem/intelligence/importance_evaluator.py`
- **函数**: `_rule_based_evaluation(self, content: str, metadata: Optional[Dict], context: Optional[Dict]) -> float`
- **输入**: `content`（记忆内容）, `metadata`（可选元数据）, `context`（可选上下文）
- **输出**: `float`，范围 [0, 1]
- **前置条件**: `self.criteria_weights` 已初始化（默认 `{"relevance": 0.3, "novelty": 0.2, "emotional_impact": 0.15, "actionable": 0.15, "factual": 0.1, "personal": 0.1}`）
- **后置条件**: 返回值等于六个维度分数的加权和，clamped 到 [0, 1]
- **错误条件**: 若某个 `_evaluate_*` 方法抛出异常，该维度分数为 0.0

### 行为变更

- **修复前**:
  ```python
  def _rule_based_evaluation(self, content, metadata, context):
      score = 0.0
      if len(content) > 100: score += 0.1
      # ... 硬编码关键词匹配 ...
      for keyword in important_keywords:
          if keyword in content_lower: score += 0.1
      # ... metadata/context 因子 ...
      return min(score, 1.0)
  ```
  使用硬编码的关键词列表和简单加分逻辑，未利用六个 `_evaluate_*` 维度方法。

- **修复后**:
  ```python
  def _rule_based_evaluation(self, content, metadata, context):
      scores = {
          "relevance": self._evaluate_relevance(content, context),
          "novelty": self._evaluate_novelty(content, metadata),
          "emotional_impact": self._evaluate_emotional_impact(content),
          "actionable": self._evaluate_actionable(content),
          "factual": self._evaluate_factual(content),
          "personal": self._evaluate_personal(content, metadata),
      }
      weighted_total = sum(
          scores[dim] * weight
          for dim, weight in self.criteria_weights.items()
          if dim in scores
      )
      return max(0.0, min(1.0, weighted_total))
  ```
  调用六个 `_evaluate_*` 方法并使用 `criteria_weights` 加权计算。

- **`get_importance_breakdown()` 更新**:
  ```python
  def get_importance_breakdown(self, content, metadata, context):
      breakdown = {}
      for criterion, weight in self.criteria_weights.items():
          if criterion == "relevance":
              breakdown[criterion] = self._evaluate_relevance(content, context)
          # ... 其他维度 ...
      breakdown["weighted_total"] = sum(
          breakdown[dim] * weight
          for dim, weight in self.criteria_weights.items()
          if dim in breakdown
      )
      return breakdown
  ```

### NFR

- **NFR-6.1 向后兼容**: `_rule_based_evaluation()` 返回值范围保持 [0, 1]
- **NFR-6.2 一致性**: `_rule_based_evaluation()` 和 `_llm_based_evaluation()` 使用相同的评估框架（六个维度 + 权重）
- **NFR-6.3 可配置性**: `criteria_weights` 可通过 `__init__` 的 config 参数覆盖
- **NFR-6.4 零输入**: 当输入内容无任何关键词匹配时，所有维度分数为 0.0，返回 0.0

### 边界条件

- 现有 `_evaluate_*` 方法使用简单关键词匹配，分数可能偏低（如 `_evaluate_relevance` 最高 1.0 需要 4 个关键词命中），这是可接受的——规则引擎的精度天然低于 LLM
- `_rule_based_evaluation()` 的旧逻辑中有 metadata/context 因子（`priority`, `user_engagement`），重构后这些因子已融入 `_evaluate_relevance()` 等维度方法中（如 `context` 传递给 `_evaluate_relevance`）
- `_llm_based_evaluation()` 的 fallback 逻辑不变——当 LLM 响应解析失败时仍回退到 `_rule_based_evaluation()`

---

## FC-7: Issue #1149 — Ebbinghaus 衰减算法

### 函数契约

- **文件**:
  - `src/powermem/agent/implementations/multi_agent.py:918` — `update_memory_decay()`
  - `src/powermem/agent/implementations/multi_user.py:~919` — `update_memory_decay()`
  - `src/powermem/intelligence/ebbinghaus_algorithm.py` — `EbbinghausAlgorithm` 类
- **函数**: `update_memory_decay(self) -> Dict[str, Any]`
- **输入**: 遍历 `self.scope_memories`（multi_agent）或 `self.user_memories`（multi_user）中所有记忆
- **输出**: `Dict` 包含 `updated_memories`, `forgotten_memories`, `reinforced_memories` 计数
- **前置条件**: `EbbinghausAlgorithm` 已通过 `intelligent_manager` 或直接构造初始化
- **后置条件**: 每个记忆的 `retention_score` 使用 Ebbinghaus 公式 `R = e^(-t/S)` 计算
- **错误条件**: 若 `EbbinghausAlgorithm` 未初始化，回退到合理默认行为（使用配置默认值构造实例）

### 行为变更

- **修复前**:
  ```python
  # Simple decay calculation (this should be replaced with proper Ebbinghaus algorithm)
  decay_rate = 0.1
  if last_accessed:
      time_since_access = (datetime.now() - last_accessed_dt).total_seconds() / 3600
  else:
      time_since_access = 24
  new_score = current_score * (1 - decay_rate * time_since_access / 24)
  new_score = max(0.0, min(1.0, new_score))
  ```
  线性衰减公式 `new_score = current_score * (1 - 0.1 * t/24)`，可产生负值（虽有 clamp），不使用记忆类型乘数和强化因子。

- **修复后**:
  ```python
  from powermem.intelligence import EbbinghausAlgorithm

  # 在 __init__ 或 update_memory_decay 中初始化
  if not hasattr(self, '_ebbinghaus'):
      intelligent_config = self._get_intelligent_memory_config()
      self._ebbinghaus = EbbinghausAlgorithm(intelligent_config)

  # 替换衰减计算
  new_score = self._ebbinghaus.calculate_current_retention(memory_data)
  decay_rate = self._ebbinghaus._resolve_decay_rate(memory_data)

  decay_result = {
      'new_score': new_score,
      'decay_rate': decay_rate,
      'forgotten': self._ebbinghaus.should_forget(memory_data),
      'reinforced': False,  # reinforced 由 review 逻辑处理
  }
  ```

- **`calculate_current_retention()` 算法**:
  ```
  R = stored_retention * e^(-t/S)
  其中:
    S = decay_rate * memory_type_multiplier * (1 + reinforcement_factor * ln(1 + access_count))
    t = hours_since_last_review (或 creation_time)
    stored_retention = metadata.intelligence.current_retention (初始值 = initial_retention * importance_score)
  ```

### NFR

- **NFR-7.1 向后兼容**: `update_memory_decay()` 方法签名不变，返回值结构不变
- **NFR-7.2 算法一致性**: 使用与 `IntelligentMemoryManager` 相同的 `EbbinghausAlgorithm` 实例和参数
- **NFR-7.3 可配置性**: 衰减参数（`decay_rate`, `reinforcement_factor`, `decay_rate_multipliers`）从 `intelligent_memory` 或 `memory_decay` 配置中读取
- **NFR-7.4 性能**: `calculate_current_retention()` 是 O(1) 的数学计算，无需遍历历史记录
- **NFR-7.5 错误容错**: 若 `EbbinghausAlgorithm` 初始化失败，使用默认配置构造实例而非抛出异常

### 边界条件

- `EbbinghausAlgorithm` 需要 memory dict 包含 `metadata.intelligence` 结构（`current_retention`, `memory_type`, `access_count`, `last_reviewed` 等）。agent memory 的数据格式需兼容此结构。
- 记忆类型乘数：`working=1`（衰减最快）, `short_term=7`, `long_term=60`（衰减最慢）
- 强化因子：`access_count > 0` 时，`S = base_rate * (1 + 0.3 * ln(1 + access_count))`，衰减速度降低
- `forgotten` 判定：`current_retention < working_threshold (0.3)` → `should_forget = True`
- 现有 `cleanup_forgotten_memories()` 依赖 `retention_score < 0.1` 判定删除，`< 0.3` 判定归档——这些阈值与 Ebbinghaus 的 `working_threshold` 一致
- multi_agent.py 和 multi_user.py 的 `update_memory_decay()` 实现几乎相同，可考虑后续抽取公共方法

---

## 非功能需求（NFR）汇总

| NFR | 描述 | 涉及 FC |
|-----|------|---------|
| NFR-1 | 向后兼容：不破坏现有 API 和行为 | FC-1, FC-2, FC-3, FC-4, FC-5, FC-6, FC-7 |
| NFR-2 | 最小变更：每个 issue 修复范围最小化 | FC-1, FC-2, FC-3, FC-4 |
| NFR-3 | 代码质量：无新 lint 警告或类型错误 | FC-2, FC-6, FC-7 |
| NFR-4 | 安全：API 响应不泄露内部信息 | FC-5 |
| NFR-5 | 可观测性：保留现有日志行为 | FC-5, FC-7 |
| NFR-6 | 数据完整性：关键字段不为 null | FC-3, FC-4 |
| NFR-7 | 算法一致性：衰减逻辑统一 | FC-6, FC-7 |

## 测试策略

### FC-1 测试
- 验证 5 个 feature 图标 div 的 className 不包含 `undefined`
- 移动端（≤800px）布局验证

### FC-2 测试
- `from powermem.integrations.rerank import NimRerank` 导入成功
- `from powermem.integrations.rerank import *` 包含 `NimRerank` 和 `NimRerankConfig`

### FC-3 测试
- OceanBase 环境：调用 `forget(memory_id)` 后查询 metadata JSON column 包含 `should_forget: true`
- SQLite 环境：回归测试确认 `forget()` 仍正常工作

### FC-4 测试
- multi-agent 模式：存储记忆后查询 `retention_score` 不为 null
- multi-user 模式：同上
- LLM 未启用时：`retention_score` 为默认值 1.0

### FC-5 测试
- 所有 API 端点：触发异常后验证响应中 `message` 不包含 `str(e)` 内容
- health check：验证 `error_message` 为通用消息
- 日志验证：原始异常仍记录到 `logger.error`

### FC-6 测试
- `_rule_based_evaluation()` 返回值等于六维加权和
- `get_importance_breakdown()` 返回值包含 `weighted_total` 键
- 零输入场景返回 0.0

### FC-7 测试
- `update_memory_decay()` 使用 Ebbinghaus 公式而非线性公式
- `working` 类型记忆衰减快于 `long_term`
- `access_count > 0` 时衰减速度降低
- `EbbinghausAlgorithm` 未初始化时不抛出异常
