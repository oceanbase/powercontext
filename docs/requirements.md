# 需求文档 — PowerMem Issue Batch Fix (2026-07-25)

**分支**: `fix/issue-batch-2026-07-25`
**日期**: 2026-07-25
**来源**: GitHub Issues #1178, #1158, #1151, #1143, #1137, #1141, #1149

---

## 项目概述

本批次修复涵盖 7 个 GitHub Issues，分为三个优先级层次：
- **P0 — 简单修复**（3 个）：文档/导出/数据一致性问题，改动小、风险低
- **P1 — 中等修复**（3 个）：功能缺陷和安全漏洞，需跨文件修改
- **P2 — 复杂修复**（1 个）：算法替换，涉及核心记忆衰减逻辑

---

## P0 — 简单修复

### US-001: Homepage Feature Icons 渲染 `undefined` CSS Class（Issue #1178）

**作为** 访问 PowerMem 文档网站的用户，
**我想要** 看到正确渲染的 feature icons，
**以便** 获得良好的第一印象和专业的文档体验。

**背景**: PR #1170 重设计网站时删除了 `icon-developer`、`icon-intelligent` 等 CSS classes，但 `Features/index.tsx:103` 仍在使用动态 class 查找 `${styles[`icon-${feature.key}`]}`。由于 `styles` 对象中不存在这些 key，模板字面量解析为 `undefined` 字符串。

**文件**: `docs/website/src/components/Features/index.tsx:103`

**验收标准**:

- AC-1.1: Given `styles.module.css` 中不存在 `icon-developer` 等 class，When 页面渲染 Features 组件，Then 所有 icon div 的 class 属性中不包含字面量 `undefined` 字符串
- AC-1.2: Given 修复后使用 `${styles.icon}` 静态 class，When 页面渲染，Then 每个 icon div 具有相同的统一 CSS class `styles.icon`
- AC-1.3: Given 5 个 feature items，When 页面渲染，Then 所有 5 个 icon 均正确显示样式（无空白/错位）

**边界条件**:
- CSS modules 编译后的 class name 哈希可能变化 — 修复不引入新的动态查找
- 不应改变 icon 的视觉外观，仅修复 class 引用

---

### US-002: NIM Reranker 未导出到 Public Package（Issue #1158）

**作为** 使用 PowerMem rerank 包的开发者，
**我想要** 直接从 `powermem.integrations.rerank` 导入 `NimRerank` 和 `NimRerankConfig`，
**以便** 不需要深入子模块路径。

**背景**: PR #1157 添加了 `NimRerank` 类（`rerank/nim.py`）和 `NimRerankConfig`（`rerank/config/providers.py`），但未更新 `rerank/__init__.py` 的导入和 `__all__` 列表。

**文件**: `src/powermem/integrations/rerank/__init__.py`

**验收标准**:

- AC-2.1: Given `rerank/nim.py` 导出 `NimRerank`，When `__init__.py` 被导入，Then `from powermem.integrations.rerank import NimRerank` 成功且不抛出 `ImportError`
- AC-2.2: Given `rerank/config/providers.py` 导出 `NimRerankConfig`，When `__init__.py` 被导入，Then `from powermem.integrations.rerank import NimRerankConfig` 成功
- AC-2.3: Given `__all__` 列表已更新，When `from powermem.integrations.rerank import *` 被调用，Then `NimRerank` 和 `NimRerankConfig` 均在导出的命名空间中
- AC-2.4: Given 其他已有导出（`QwenRerank`, `JinaRerank` 等），When `__init__.py` 被导入，Then 所有原有导出仍可用（无回归）

**边界条件**:
- 导入顺序：`NimRerank` 应在 `ZaiRerank` 之后、`BaseRerankConfig` 之前，遵循现有模式
- `NimRerankConfig` 应在 `ZaiRerankConfig` 之后导入

---

### US-003: `should_forget` Marker 在 OceanBase 上丢失（Issue #1151）

**作为** 使用 OceanBase 存储后端的用户，
**我想要** 标记遗忘的 memories 在查询时仍能正确返回 `should_forget` 状态，
**以便** 遗忘功能在所有存储后端一致工作。

**背景**: `_forget_marker_updates()` 返回 `{"should_forget": True, "marked_for_forgetting_at": ...}` 作为 top-level 字段。但 OceanBase 的 `_build_record_for_insert()` 只映射已知字段到 DB 列，未知的 top-level 字段被静默丢弃。修复方案是将这些值同时写入 `metadata` JSON column。

**文件**: `src/powermem/core/memory.py:56`（`_forget_marker_updates()`）

**验收标准**:

- AC-3.1: Given OceanBase 存储后端，When `_forget_marker_updates()` 被调用，Then 返回值包含 `metadata` dict，其中包含 `should_forget: True` 和 `marked_for_forgetting_at` 时间戳
- AC-3.2: Given 返回的 dict 包含 `metadata`，When `_build_record_for_insert()` 处理该记录，Then `metadata` JSON column 中包含遗忘标记
- AC-3.3: Given memory 已被标记遗忘，When 从 OceanBase 查询该 memory，Then `metadata.should_forget` 为 `True`
- AC-3.4: Given 现有的 top-level 字段 `should_forget` 和 `marked_for_forgetting_at`，When 修复后，Then 这些字段仍保留在返回值中（向后兼容）

**边界条件**:
- `metadata` 字段可能已包含其他数据 — 需要合并而非覆盖
- 时间戳格式应与 `get_current_datetime().isoformat()` 一致
- SQLite/其他后端不受影响，但修复不应破坏它们

---

## P1 — 中等修复

### US-004: AgentMemory Persist 丢失 `retention_score`（Issue #1143）

**作为** 使用 AgentMemory 的开发者，
**我想要** persist 写入的 memories 包含正确的 `retention_score`，
**以便** 记忆衰减和检索排序正常工作。

**背景**: `_persist_memory_to_storage()` 在 persist 前构建 memory data dict，但未从 `enhanced_metadata.intelligence.current_retention` 提取 `retention_score`。导致写入 DB 的 `retention_score` 为 `null`。

**文件**:
- `src/powermem/agent/implementations/multi_agent.py`（~L226-232, ~L349-355）
- `src/powermem/agent/implementations/multi_user.py`（~L161-169, ~L267-270）

**验收标准**:

- AC-4.1: Given `enhanced_metadata` 包含 `intelligence.current_retention`，When `_persist_memory_to_storage()` 被调用，Then `memory_data['retention_score']` 被设置为 `intelligence.current_retention` 的值
- AC-4.2: Given `enhanced_metadata` 不包含 `intelligence` 字段，When persist，Then `retention_score` 回退到默认值 `1.0`
- AC-4.3: Given `multi_agent.py` 和 `multi_user.py` 两个文件，When 修复应用后，Then 两个实现中的 `_persist_memory_to_storage()` 都正确填充 `retention_score`
- AC-4.4: Given persist 成功，When 从 DB 读取 memory，Then `retention_score` 不为 `null`

**边界条件**:
- `intelligence.current_retention` 可能为 `None` — 需要回退到 `1.0`
- `enhanced_metadata` 本身可能为 `None` — 需要安全访问
- 修改不应影响其他 metadata 字段的写入

---

### US-005: API 响应暴露原始异常信息（Issue #1137）

**作为** 使用 PowerMem API 的用户/攻击者，
**我想要** API 错误响应不泄露内部实现细节，
**以便** 系统安全性得到保障。

**背景**: `health_check.py:131` 和 `:224` 使用 `str(e)` 作为错误消息返回给客户端。`memory_service.py`、`user_service.py`、`agent_service.py`、`search_service.py` 中也存在类似模式（共 20+ 处）。PR #1133 已有 `service_errors.py` helper 函数可参考。

**文件**:
- `src/server/utils/health_check.py`（:131, :224）
- `src/server/services/memory_service.py`（多处）
- `src/server/services/user_service.py`（多处）
- `src/server/services/agent_service.py`（多处）
- `src/server/services/search_service.py`

**验收标准**:

- AC-5.1: Given 任何 API 端点发生异常，When 异常被捕获，Then HTTP 响应 body 中不包含 `str(e)` 原始异常文本
- AC-5.2: Given 异常被捕获，When 日志记录，Then 原始异常信息（含 traceback）仍记录到服务端日志
- AC-5.3: Given `health_check.py` 中的 `DependencyStatus`，When 依赖检查失败，Then `error_message` 使用通用描述（如 "Database connection failed"）而非 `str(e)`
- AC-5.4: Given 修复后，When API 返回错误，Then 响应格式保持不变（`ServiceResult` 结构），仅消息内容脱敏

**边界条件**:
- 部分 `str(e)` 出现在 `logger.error()` 中 — 这些应保留，仅移除客户端响应中的
- `service_errors.py` 已有 helper 模式，应复用而非重新发明
- 需要逐文件审查，确保不遗漏

---

### US-006: Rule-based Importance 评估未使用六维加权评分（Issue #1141）

**作为** 不使用 LLM 的 PowerMem 用户，
**我想要** rule-based importance 评估使用与 LLM 评估相同的六维加权评分体系，
**以便** 评估结果一致且可解释。

**背景**: `_rule_based_evaluation()` 使用硬编码的简单规则（长度、关键词、标点等），而 `get_importance_breakdown()` 已实现了六个 `_evaluate_*` 维度方法（relevance, novelty, emotional_impact, actionable, factual, personal）和 `criteria_weights` 加权。两个方法应统一。

**文件**: `src/powermem/intelligence/importance_evaluator.py`

**验收标准**:

- AC-6.1: Given `_rule_based_evaluation()` 被调用，When LLM 不可用，Then 评估结果使用六个 `_evaluate_*` 方法计算各维度分数并用 `criteria_weights` 加权求和
- AC-6.2: Given `get_importance_breakdown()` 被调用，When 返回 breakdown dict，Then dict 包含 `weighted_total` 字段，值为加权总分
- AC-6.3: Given 六维评分结果，When 与旧 rule-based 结果对比，Then 新结果范围仍在 `[0.0, 1.0]`
- AC-6.4: Given `_rule_based_evaluation()` 重构后，When `_llm_based_evaluation()` 回退到 rule-based，Then 回退路径使用相同的六维逻辑

**边界条件**:
- 各 `_evaluate_*` 方法返回值已在 `[0.0, 1.0]` 范围内 — 加权后需 `min(score, 1.0)` 截断
- `criteria_weights` 之和为 1.0（relevance=0.3, novelty=0.2, emotional_impact=0.15, actionable=0.15, factual=0.1, personal=0.1）— 需验证归一化
- 旧 `_rule_based_evaluation` 中的 keyword/metadata 逻辑将被移除，需确保六维方法覆盖相同语义

---

## P2 — 复杂修复

### US-007: `update_memory_decay()` 使用线性公式而非 Ebbinghaus 算法（Issue #1149）

**作为** 关注记忆衰减准确性的用户，
**我想要** `update_memory_decay()` 使用 `EbbinghausAlgorithm.calculate_current_retention()` 替代硬编码的线性衰减，
**以便** 记忆衰减遵循认知科学模型。

**背景**: `multi_agent.py:936-951` 和 `multi_user.py:919-934` 中的 `update_memory_decay()` 使用硬编码线性公式 `new_score = current_score * (1 - decay_rate * time_since_access / 24)`。项目已有 `EbbinghausAlgorithm.calculate_current_retention()` 实现（`intelligence/ebbinghaus_algorithm.py:340`），但未被调用。

**文件**:
- `src/powermem/agent/implementations/multi_agent.py`（~L936-951）
- `src/powermem/agent/implementations/multi_user.py`（~L919-934）

**验收标准**:

- AC-7.1: Given `update_memory_decay()` 被调用，When 计算衰减，Then 使用 `EbbinghausAlgorithm.calculate_current_retention(memory)` 而非线性公式
- AC-7.2: Given Ebbinghaus 算法需要 `memory` dict（含 `created_at`, `last_accessed`, `access_count`, `metadata.intelligence`），When 传入现有 `memory_data`，Then 算法能正确解析所有必需字段
- AC-7.3: Given `multi_agent.py` 和 `multi_user.py` 两个文件，When 修复后，Then 两个实现都使用 `EbbinghausAlgorithm`
- AC-7.4: Given Ebbinghaus 算法返回值在 `[0.0, 1.0]`，When 更新 `retention_score`，Then 值被限制在有效范围内
- AC-7.5: Given `EbbinghausAlgorithm` 实例需要配置参数，When `update_memory_decay()` 被调用，Then 算法实例从 `IntelligenceManager` 或 `EbbinghausIntelligencePlugin` 获取（不重新创建）

**边界条件**:
- `EbbinghausAlgorithm` 依赖 `IntelligentConfig` — 需确认 `multi_agent.py` 和 `multi_user.py` 中有可用实例
- `memory_data` dict 结构可能与 `EbbinghausAlgorithm` 期望的不完全一致 — 可能需要适配层
- `calculate_current_retention()` 内部已做 `max(0.0, min(1.0, ...))` 截断
- 线性公式移除后，`decay_rate` 字段仍需保留（可能被其他逻辑使用）

---

## 非功能需求（NFR）

### NFR-1: 向后兼容
- 所有修复不得破坏现有 API 接口或数据格式
- `__init__.py` 新增导出不得影响已有导入路径
- `_forget_marker_updates()` 返回值新增 `metadata` 字段但保留原有 top-level 字段

### NFR-2: 最小变更原则
- 每个修复仅针对对应 issue 描述的问题，不做额外重构
- US-006 的六维统一是 issue 作者明确提出的范围

### NFR-3: 代码质量
- 修复代码应遵循项目现有风格（类型注解、docstring、logging）
- 新增的 `EbbinghausAlgorithm` 调用应有适当的错误处理和 fallback

### NFR-4: 安全（US-005 专项）
- API 响应中不得暴露任何内部路径、堆栈跟踪或原始异常文本
- 日志中必须保留完整异常信息用于调试

---

## 术语表

| 术语 | 说明 |
|------|------|
| `retention_score` | 记忆保留分数，0.0-1.0，基于 Ebbinghaus 遗忘曲线 |
| `EbbinghausAlgorithm` | 基于认知科学的记忆衰减算法实现 |
| `OceanBase` | 分布式数据库存储后端 |
| `metadata` JSON column | 数据库中存储记忆元数据的 JSON 列 |
| `criteria_weights` | 六维评估各维度的权重配置 |
| `ServiceResult` | API 响应的标准封装结构 |
| `DependencyStatus` | 健康检查依赖状态的数据类 |
