# 需求文档 — powermem issue batch fix (2026-07-25)

## 项目概述

本需求文档涵盖 7 个 GitHub issues 的结构化需求分析，分为三个批次：
- **第一批（简单修复）**：#1178, #1158, #1151
- **第二批（中等修复）**：#1143, #1137, #1141
- **第三批（复杂修复）**：#1149

---

## 第一批：简单修复

### Issue #1178 — Homepage feature icons render `undefined` CSS class

**用户故事**：
作为网站访客，我希望首页特性图标正确渲染，以便看到完整的视觉设计而不是破坏的样式。

**优先级**：P1（视觉回归，影响用户体验）

**涉及文件**：
- `docs/website/src/components/Features/index.tsx:103`
- `docs/website/src/components/Features/styles.module.css`

**问题描述**：
`index.tsx` 第 103 行使用 `${styles[`icon-${feature.key}`]}` 动态查找 CSS class（如 `.icon-developer`），但 CSS 中只定义了 `.icon` class。PR #1170 重设计网站时删除了各 feature 的独立 CSS class，但 JSX 中的引用未同步清除，导致浏览器渲染 `undefined` 字面量作为 class 名。

**验收标准**：
- **AC-1.1**：Given 访客打开首页，When 页面加载完成，Then 5 个 feature 图标的容器 div 应仅包含 `styles.icon` class，不包含 `undefined` 或不存在的 class
- **AC-1.2**：Given 访客打开首页，When 页面加载完成，Then 所有 5 个 feature 图标（Developer、Intelligent、Multi-Agent、Multimodal、Storage）正确显示蓝色圆形背景和图标
- **AC-1.3**：Given 访客在移动端（≤800px）打开首页，When 页面响应式布局生效，Then 图标在单列布局中仍然正确显示

**边界条件与风险点**：
- 需确认 `styles.icon` 的 CSS 作用域（CSS Modules）不会与其他组件冲突
- 无其他组件使用类似的动态 CSS class 查找模式

---

### Issue #1158 — NIM reranker missing from public rerank package exports

**用户故事**：
作为开发者，我希望 `from powermem.integrations.rerank import NimRerank` 直接可用，以便无需了解内部模块结构即可使用 NIM reranker。

**优先级**：P1（API 可用性，影响开发者体验）

**涉及文件**：
- `src/powermem/integrations/rerank/__init__.py`
- `src/powermem/integrations/rerank/nim.py`（已存在，无需修改）
- `src/powermem/integrations/rerank/config/providers.py`（`NimRerankConfig` 已定义，无需修改）

**问题描述**：
PR #1157 添加了 `NimRerank` 和 `NimRerankConfig`，但未更新 `__init__.py` 的导入和 `__all__` 列表，导致用户无法从包级别直接导入。

**验收标准**：
- **AC-2.1**：Given `__init__.py` 已更新，When 执行 `from powermem.integrations.rerank import NimRerank`，Then 导入成功且 `NimRerank` 指向正确的类
- **AC-2.2**：Given `__init__.py` 已更新，When 执行 `from powermem.integrations.rerank import NimRerankConfig`，Then 导入成功且 `NimRerankConfig` 指向正确的配置类
- **AC-2.3**：Given `__init__.py` 已更新，When 执行 `from powermem.integrations.rerank import *`，Then `NimRerank` 和 `NimRerankConfig` 均在导出列表中
- **AC-2.4**：Given `__init__.py` 已更新，When 执行 `help(powermem.integrations.rerank)`，Then `NimRerank` 和 `NimRerankConfig` 出现在模块文档中

**边界条件与风险点**：
- `NimRerankConfig` 在 `config/providers.py` 中定义，导入路径为 `.config.providers`（不是 `.config`）
- 需确保 `NimRerank` 的依赖（`httpx`）在导入时不会报错（当前已有 try/except 处理）

---

### Issue #1151 — `should_forget` marker lost on OceanBase storage

**用户故事**：
作为使用 OceanBase 存储的用户，我希望 `forget()` 操作的标记（`should_forget`、`marked_for_forgetting_at`）能正确持久化到数据库，以便后续查询和清理逻辑能正确识别待遗忘的记忆。

**优先级**：P0（数据完整性缺陷，影响 forget 功能在 OceanBase 上的正确性）

**涉及文件**：
- `src/powermem/core/memory.py:56`（`_forget_marker_updates()`）
- `src/powermem/core/memory.py`（`_simple_add()` 方法）
- `src/powermem/storage/oceanbase/oceanbase.py:848`（`_build_record_for_insert()`）

**问题描述**：
`_forget_marker_updates()` 返回 `should_forget: True` 和 `marked_for_forgetting_at: <timestamp>` 作为 top-level 字段。在 `_simple_add()` 中，这些字段通过 `enhanced_metadata` 传递给 `storage.add_memory()`。然而，OceanBase 的 `_build_record_for_insert()` 只映射已知字段（`user_id`, `agent_id`, `run_id`, `hash`, `category` 等）到 DB 列，top-level 的 `should_forget` 和 `marked_for_forgetting_at` 被静默丢弃。

**验收标准**：
- **AC-3.1**：Given OceanBase 存储后端，When 调用 `forget(memory_id)` 后查询该记忆的 metadata JSON column，Then metadata 中包含 `should_forget: true` 字段
- **AC-3.2**：Given OceanBase 存储后端，When 调用 `forget(memory_id)` 后查询该记忆的 metadata JSON column，Then metadata 中包含 `marked_for_forgetting_at` 字段且值为有效 ISO 时间戳
- **AC-3.3**：Given SQLite/其他存储后端，When 调用 `forget(memory_id)`，Then `should_forget` 和 `marked_for_forgetting_at` 仍正确持久化（不引入回归）
- **AC-3.4**：Given 记忆已被标记为 `should_forget`，When 执行 `get_all()` 或 `search()`，Then 该记忆的 metadata 中包含遗忘标记

**边界条件与风险点**：
- 修复方式：在 `_forget_marker_updates()` 的返回值中同时写入 metadata dict，确保 `should_forget` 和 `marked_for_forgetting_at` 出现在 metadata JSON column 中
- 需注意 `_build_record_for_insert()` 序列化 metadata 时的 datetime 处理
- 需验证 `on_get` 触发 `delete_flag` 的完整链路

---

## 第二批：中等修复

### Issue #1143 — `retention_score` written as null despite available `current_retention`

**用户故事**：
作为系统管理员，我希望持久化到数据库的记忆包含正确的 `retention_score`，以便记忆衰减和清理逻辑能基于准确的保留分数运行。

**优先级**：P1（数据质量缺陷，影响记忆衰减准确性）

**涉及文件**：
- `src/powermem/agent/implementations/multi_agent.py`（`_persist_memory_to_storage()` 方法）
- `src/powermem/agent/implementations/multi_user.py`（`_persist_memory_to_storage()` 方法）

**问题描述**：
`_persist_memory_to_storage()` 在构建 metadata dict 时，`retention_score` 的值来自 `memory_data.get('retention_score')`，但此时 `memory_data` 可能尚未填充 `retention_score`（该值通常在 `process_memory()` 中从 `enhanced_metadata.intelligence.current_retention` 提取并赋值到 `memory_data`）。由于 `_persist_memory_to_storage()` 在 `memory_data` 完整构建之前被调用，`retention_score` 为 null。

**验收标准**：
- **AC-4.1**：Given multi-agent 模式，When `process_memory()` 存储一条记忆，Then 数据库 metadata 中 `retention_score` 不为 null，而是 `enhanced_metadata.intelligence.current_retention` 的值
- **AC-4.2**：Given multi-user 模式，When `process_memory()` 存储一条记忆，Then 数据库 metadata 中 `retention_score` 不为 null
- **AC-4.3**：Given intelligence 数据中 `current_retention` 为 0.8，When 持久化记忆，Then metadata 中 `retention_score` 为 0.8
- **AC-4.4**：Given intelligence 数据中无 `current_retention`（如 LLM 未启用），When 持久化记忆，Then `retention_score` 使用默认值 1.0（非 null）

**边界条件与风险点**：
- `enhanced_metadata` 是 `intelligent_manager.process_metadata()` 的返回值，其中 `intelligence.current_retention` 由 `EbbinghausAlgorithm` 计算
- 需确保修复不影响 `_persist_memory_to_storage()` 的其他 metadata 字段
- multi_agent.py 和 multi_user.py 的 `_persist_memory_to_storage()` 实现类似但不完全相同，需分别修复

---

### Issue #1137 — Raw exceptions exposed in API responses（安全漏洞）

**用户故事**：
作为系统管理员，我希望 API 响应不暴露原始异常信息，以便防止内部实现细节泄露给外部用户。

**优先级**：P0（安全漏洞，信息泄露风险）

**涉及文件**：
- `src/server/utils/health_check.py`（:131, :224）— `_check_database_sync()`, `_check_llm_sync()`
- `src/server/services/memory_service.py`（:318, :368, :503, :563, :626, :1370, :1450, :1566, :1651, :1762）
- `src/server/services/user_service.py`（:67, :153, :215, :266, :323, :381, :420）
- `src/server/services/agent_service.py`（:80, :165, :315）
- `src/server/services/search_service.py`（:112）
- `src/server/utils/service_errors.py`（已有 helper 函数参考）

**问题描述**：
多处 API 端点的异常处理中使用 `str(e)` 或 `f"...: {str(e)}"` 将原始异常信息直接返回给客户端。这些信息可能包含数据库连接字符串、内部路径、堆栈跟踪等敏感信息。PR #1133 已有 `service_errors.py` 中的 helper 函数模式可参考。

**验收标准**：
- **AC-5.1**：Given 任何 API 端点，When 发生未处理异常，Then 响应中的 `message` 字段不包含原始异常的 `str(e)` 内容
- **AC-5.2**：Given 任何 API 端点，When 发生未处理异常，Then 原始异常信息仅记录到服务器日志（`logger.error`/`logger.exception`）
- **AC-5.3**：Given health check 端点，When 数据库连接失败，Then 响应中 `error_message` 为通用消息（如 "Database connection failed"），不包含连接字符串或堆栈跟踪
- **AC-5.4**：Given memory/user/agent/search 服务，When 操作失败，Then 响应中的 `message` 使用通用前缀（如 "Failed to create memory"），不附加 `str(e)`
- **AC-5.5**：Given 错误响应，When 客户端解析 JSON，Then 响应结构符合 `ErrorResponse` 模型（包含 `error.code`, `error.message`, `error.details`）

**边界条件与风险点**：
- 需逐一排查所有 `str(e)` 出现位置，区分哪些是安全可暴露的（如验证错误），哪些是敏感的
- `health_check.py` 中已有截断逻辑（`error_msg[:197] + "..."`），但仍暴露了部分异常内容
- `service_errors.py` 中的 `public_startup_error_message()` 模式可作为参考
- 需确保不破坏已有的 `APIError` 错误处理流程

---

### Issue #1141 — Unify rule-based importance evaluation with six-dimension weighted scoring

**用户故事**：
作为系统开发者，我希望 `_rule_based_evaluation()` 使用已有的六个维度评估方法和配置权重，以便规则引擎和 LLM 引擎使用一致的评估框架。

**优先级**：P1（架构一致性，影响评估质量）

**涉及文件**：
- `src/powermem/intelligence/importance_evaluator.py`

**问题描述**：
`_rule_based_evaluation()` 使用硬编码的关键词匹配和简单加分逻辑，未利用已有的六个 `_evaluate_*` 维度方法（`_evaluate_relevance`, `_evaluate_novelty`, `_evaluate_emotional_impact`, `_evaluate_actionable`, `_evaluate_factual`, `_evaluate_personal`）。同时 `get_importance_breakdown()` 返回各维度分数但未计算 `weighted_total`，与 `_rule_based_evaluation()` 的结果不一致。

**验收标准**：
- **AC-6.1**：Given LLM 不可用，When 调用 `evaluate_importance(content)`，Then 内部调用六个 `_evaluate_*` 维度方法并使用 `criteria_weights` 加权计算总分
- **AC-6.2**：Given `criteria_weights` 配置为 `{"relevance": 0.3, "novelty": 0.2, "emotional_impact": 0.15, "actionable": 0.15, "factual": 0.1, "personal": 0.1}`，When 调用 `_rule_based_evaluation()`，Then 返回值等于各维度分数的加权和（clamped 到 [0, 1]）
- **AC-6.3**：Given 调用 `get_importance_breakdown(content)`，When 返回 breakdown dict，Then dict 中包含 `weighted_total` 键，其值等于各维度分数的加权和
- **AC-6.4**：Given `_rule_based_evaluation()` 重构后，When 与 `_llm_based_evaluation()` 对比，Then 两者使用相同的评估框架（六个维度 + 权重）
- **AC-6.5**：Given `_rule_based_evaluation()` 重构后，When 输入内容无任何关键词匹配，Then 返回值为 0.0（而非旧逻辑的 0.0 + 各种小加分）

**边界条件与风险点**：
- 现有 `_evaluate_*` 方法使用简单的关键词匹配，分数可能偏低，需确认是否需要增强
- `criteria_weights` 在 `__init__` 中定义，可被配置覆盖
- `_rule_based_evaluation()` 的旧逻辑中有一些 metadata/context 因子（如 `priority`, `user_engagement`），重构后需决定是否保留

---

## 第三批：复杂修复

### Issue #1149 — `update_memory_decay()` uses linear formula instead of EbbinghausAlgorithm

**用户故事**：
作为系统开发者，我希望 `update_memory_decay()` 使用项目中已有的 `EbbinghausAlgorithm` 实现遗忘曲线衰减，以便记忆衰减行为与认知科学模型一致且可配置。

**优先级**：P1（算法正确性，影响记忆管理质量）

**涉及文件**：
- `src/powermem/agent/implementations/multi_agent.py`（`update_memory_decay()` 方法，~L936-951）
- `src/powermem/agent/implementations/multi_user.py`（`update_memory_decay()` 方法，~L919-934）
- `src/powermem/intelligence/ebbinghaus_algorithm.py`（已有 `EbbinghausAlgorithm` 类）
- `src/powermem/intelligence/__init__.py`（`EbbinghausAlgorithm` 已导出）

**问题描述**：
`update_memory_decay()` 使用硬编码的线性衰减公式：
```python
decay_rate = 0.1
time_since_access = (now - last_accessed).total_seconds() / 3600
new_score = current_score * (1 - decay_rate * time_since_access / 24)
```
该公式不使用项目中已有的 `EbbinghausAlgorithm`（基于 `R = e^(-t/S)` 的指数衰减），导致：
1. 衰减行为与 `IntelligentMemoryManager` 中的衰减不一致
2. 无法利用配置化的衰减率、记忆类型乘数、强化因子等参数
3. 线性衰减可能产生负值（虽然有 clamp），不符合遗忘曲线的数学特性

**验收标准**：
- **AC-7.1**：Given multi-agent 模式，When 调用 `update_memory_decay()`，Then 内部使用 `EbbinghausAlgorithm.calculate_current_retention(memory)` 计算新保留分数
- **AC-7.2**：Given multi-user 模式，When 调用 `update_memory_decay()`，Then 内部使用 `EbbinghausAlgorithm.calculate_current_retention(memory)` 计算新保留分数
- **AC-7.3**：Given 记忆类型为 `working`（衰减乘数=1），When 计算衰减，Then 衰减速度比 `long_term`（衰减乘数=60）快
- **AC-7.4**：Given 记忆有多次访问（`access_count > 0`），When 计算衰减，Then 强化因子（`reinforcement_factor`）使衰减速度降低（衰减率增大）
- **AC-7.5**：Given `EbbinghausAlgorithm` 未初始化（如配置缺失），When 调用 `update_memory_decay()`，Then 回退到合理的默认行为而非抛出异常
- **AC-7.6**：Given `update_memory_decay()` 重构后，When 对比 `IntelligentMemoryManager` 中的衰减逻辑，Then 两者使用相同的算法和参数

**边界条件与风险点**：
- `EbbinghausAlgorithm` 需要配置参数（`initial_retention`, `decay_rate`, `reinforcement_factor` 等），需确保从 agent manager 的 config 中正确提取
- `calculate_current_retention()` 需要 memory dict 包含 `metadata.intelligence` 结构，需确保 agent memory 的数据格式兼容
- 现有 `update_memory_decay()` 还返回 `forgotten_memories` 和 `reinforced_memories` 计数，重构后需保留这些统计
- `EbbinghausAlgorithm.calculate_current_retention()` 已包含时间衰减计算，不需要额外的循环遍历所有记忆
- 需注意 `multi_agent.py` 和 `multi_user.py` 的 `update_memory_decay()` 实现几乎相同，可考虑抽取公共方法

---

## 非功能需求（NFR）

### NFR-1: 向后兼容
- 所有修复不得破坏现有 API 接口和行为
- #1158 的新增导出是纯增量变更
- #1141 的重构需保持 `_rule_based_evaluation()` 的返回值范围（[0, 1]）

### NFR-2: 最小变更
- 每个 issue 的修复应尽量最小化变更范围
- #1178 仅修改一行 JSX
- #1151 仅修改 `_forget_marker_updates()` 函数

### NFR-3: 代码质量
- 修复不得引入新的 lint 警告或类型错误
- #1149 的重构应保持方法签名不变

### NFR-4: 安全
- #1137 的修复必须确保所有 API 响应不泄露内部实现细节
- 错误消息应足够通用，不包含路径、端口、凭据等信息

### NFR-5: 可观测性
- 所有修复保留现有的日志记录行为
- #1137 的修复需确保原始异常信息仍记录到日志

---

## 术语表

| 术语 | 定义 |
|------|------|
| OceanBase | 分布式数据库，powermem 的存储后端之一 |
| Ebbinghaus 遗忘曲线 | 基于认知科学的记忆衰减模型，公式为 R = e^(-t/S) |
| NIM | NVIDIA Inference Microservice，用于 reranking 的推理服务 |
| retention_score | 记忆保留分数，范围 [0, 1]，随时间衰减 |
| metadata JSON column | OceanBase 中存储记忆元数据的 JSON 格式列 |
| _build_record_for_insert() | OceanBase 存储适配器中构建插入记录的方法 |
| _forget_marker_updates() | 返回遗忘标记字段的辅助函数 |
| criteria_weights | 重要性评估的六维权重配置 |
