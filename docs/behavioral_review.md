# 行为审查报告 — powermem issue batch fix

**审查日期**: 2026-07-25
**分支**: fix/issue-batch-2026-07-25
**审查员**: reviewer-dev
**Phase**: 7

---

## 1. 需求对照审查（Spec → 实现）

### FC-1: Issue #1178 — CSS class undefined

| 检查项 | 结果 | 说明 |
|--------|------|------|
| AC-1.1: 图标容器 div 不含 `undefined` class | ✅ PASS | 移除 `${styles[`icon-${feature.key}`]}` 动态查找，仅用 `styles.icon` |
| AC-1.2: 5 个 feature 图标正确显示 | ✅ PASS | `.icon` class 已包含完整蓝色圆形背景样式 |
| AC-1.3: 移动端响应式正确 | ✅ PASS | `@media (max-width: 800px)` 中 `.card` 样式不受影响 |

**变更范围**: 1 行 JSX（`docs/website/src/components/Features/index.tsx:103`）
**结论**: ✅ PASS

---

### FC-2: Issue #1158 — NIM reranker 导出

| 检查项 | 结果 | 说明 |
|--------|------|------|
| AC-2.1: `from powermem.integrations.rerank import NimRerank` 成功 | ✅ PASS | `__init__.py` 新增 `from .nim import NimRerank` |
| AC-2.2: `from powermem.integrations.rerank import NimRerankConfig` 成功 | ✅ PASS | `__init__.py` 新增 `from .config.providers import NimRerankConfig` |
| AC-2.3: `import *` 包含两者 | ✅ PASS | `__all__` 中已添加 `"NimRerank"` 和 `"NimRerankConfig"` |
| AC-2.4: 模块文档包含两者 | ✅ PASS | `__all__` 导出确保 `help()` 可见 |

**变更范围**: `__init__.py` 新增 4 行（2 import + 2 `__all__` 条目）
**结论**: ✅ PASS

---

### FC-3: Issue #1151 — OceanBase forget marker

| 检查项 | 结果 | 说明 |
|--------|------|------|
| AC-3.1: OceanBase metadata 含 `should_forget: true` | ✅ PASS | `_forget_marker_updates()` 新增 `metadata` 内嵌字段 |
| AC-3.2: OceanBase metadata 含 `marked_for_forgetting_at` | ✅ PASS | 同上，ISO 时间戳 |
| AC-3.3: SQLite 不回归 | ✅ PASS | 保留 top-level 字段兼容 SQLite |
| AC-3.4: `search()`/`get_all()` 返回含遗忘标记 | ✅ PASS | metadata JSON column 包含标记 |

**变更范围**: `src/powermem/core/memory.py` — `_forget_marker_updates()` 新增 5 行
**结论**: ✅ PASS

---

### FC-4: Issue #1143 — retention_score null 修复

| 检查项 | 结果 | 说明 |
|--------|------|------|
| AC-4.1: multi-agent retention_score 不为 null | ✅ PASS | 从 `metadata.intelligence.current_retention` 提取 |
| AC-4.2: multi-user retention_score 不为 null | ✅ PASS | 同上模式 |
| AC-4.3: current_retention=0.8 → retention_score=0.8 | ✅ PASS | 直接从数据源提取 |
| AC-4.4: 无 intelligence 时默认值 1.0 | ✅ PASS | `.get('current_retention', 1.0)` |

**⚠️ 风险点**: `**memory_data.get('metadata', {})` 展开可能覆盖前面的 `retention_score`。若 `enhanced_metadata` 中包含顶层 `retention_score` key，会被展开覆盖。当前实现中 `enhanced_metadata` 不包含顶层 `retention_score`，但这是一个脆弱假设。

**变更范围**: `multi_agent.py` 和 `multi_user.py` 各修改 2 行
**结论**: ✅ PASS（附带风险提示）

---

### FC-5: Issue #1137 — API 异常信息泄露

| 检查项 | 结果 | 说明 |
|--------|------|------|
| AC-5.1: API 响应 message 不含 `str(e)` | ⚠️ CONDITIONAL | 指定文件已修复，但 `system.py:246` 遗漏 |
| AC-5.2: 原始异常记录到日志 | ✅ PASS | 所有修复点保留 `logger.error(..., exc_info=True)` |
| AC-5.3: health check 通用消息 | ✅ PASS | `"Database connection failed"`, `"LLM service check failed"` |
| AC-5.4: service 通用消息前缀 | ✅ PASS | 21 处指定泄露点全部修复 |
| AC-5.5: ErrorResponse 结构 | ✅ PASS | `APIError` 结构不变 |

**⚠️ 遗漏**: `src/server/api/v1/system.py:246` 仍使用 `message=f"Failed to delete all memories: {str(e)}"`，未在修复范围内。这是一个 API 端点，会向客户端暴露原始异常。

**变更范围**: 5 个文件，共 21 处 `str(e)` 替换
**结论**: ⚠️ CONDITIONAL APPROVE — 需修复 `system.py:246`

---

### FC-6: Issue #1141 — 重要性评估统一

| 检查项 | 结果 | 说明 |
|--------|------|------|
| AC-6.1: `_rule_based_evaluation()` 使用六维方法 | ✅ PASS | 调用 `_evaluate_relevance/novelty/emotional_impact/actionable/factual/personal` |
| AC-6.2: 加权计算返回 [0,1] | ✅ PASS | `max(0.0, min(1.0, weighted_total))` |
| AC-6.3: `get_importance_breakdown()` 含 `weighted_total` | ✅ PASS | 新增 `breakdown["weighted_total"]` |
| AC-6.4: 与 `_llm_based_evaluation()` 框架一致 | ✅ PASS | 使用相同六维 + `criteria_weights` |
| AC-6.5: 零输入返回 0.0 | ✅ PASS | 所有维度 0.0 → weighted_total = 0.0 |

**⚠️ 行为变更**: `_evaluate_personal()` 改用 `re.search(r'\b...\b', ...)` 词边界匹配，比旧的 `indicator in content_lower` 子串匹配更精确。例如：旧逻辑中 `"i" in "the item is important"` 为 True（误匹配），新逻辑为 False（正确）。此变更是改进，但可能影响依赖旧行为的下游代码。

**变更范围**: `importance_evaluator.py` — 重构 `_rule_based_evaluation()` 和 `get_importance_breakdown()`
**结论**: ✅ PASS

---

### FC-7: Issue #1149 — Ebbinghaus 衰减算法

| 检查项 | 结果 | 说明 |
|--------|------|------|
| AC-7.1: multi-agent 使用 `EbbinghausAlgorithm` | ✅ PASS | `self._ebbinghaus.calculate_current_retention(memory_data)` |
| AC-7.2: multi-user 使用 `EbbinghausAlgorithm` | ✅ PASS | 同上模式 |
| AC-7.3: working 衰减快于 long_term | ✅ PASS | `working` 乘数=1, `long_term` 乘数=60 |
| AC-7.4: access_count > 0 时衰减速度降低 | ✅ PASS | `_apply_reinforcement()` 使用 `ln(1 + access_count)` |
| AC-7.5: EbbinghausAlgorithm 未初始化不抛异常 | ✅ PASS | `except Exception` fallback 到原值 |
| AC-7.6: 与 IntelligentMemoryManager 算法一致 | ✅ PASS | 使用相同 `EbbinghausAlgorithm` 类 |

**⚠️ 风险点**:
1. `EbbinghausAlgorithm({})` 使用空 config 初始化，所有参数使用默认值（`decay_rate=1.5`, `reinforcement_factor=0.3` 等）。这与从 agent manager config 中获取参数的预期行为不同。
2. `_get_decay_rate_for_type()` 是类的公开方法（非 `_` 前缀私有方法），但实现中调用了它——这没问题，但命名不一致（SPEC 中写的是 `_resolve_decay_rate`）。
3. `except Exception` 捕获过于宽泛，可能隐藏编程错误。

**变更范围**: `multi_agent.py` 和 `multi_user.py` 各修改 ~20 行
**结论**: ✅ PASS（附带风险提示）

---

## 2. 代码质量审查

| 检查项 | 文件 | 状态 | 说明 |
|--------|------|------|------|
| 错误处理完整性 | 全部 | ✅ | 所有 except 块保留 `logger.error(..., exc_info=True)` |
| 输入验证 | FC-6 | ✅ | `max(0.0, min(1.0, ...))` clamp |
| 日志记录 | FC-5 | ✅ | 原始异常仍记录到日志 |
| 代码风格一致性 | 全部 | ✅ | 遵循项目现有模式 |
| 向后兼容性 | 全部 | ✅ | API 结构不变，仅内容变化 |
| 未使用的导入 | `multi_agent.py`, `multi_user.py` | ⚠️ Minor | `from powermem.core.memory import Memory` 模块级导入，但方法内已有局部导入 |
| `except Exception` 过宽 | `multi_agent.py`, `multi_user.py` (FC-7) | ⚠️ Minor | fallback 中 `except Exception` 可能隐藏编程错误 |
| 魔法数字 | FC-7 fallback | ⚠️ Minor | `decay_rate = 0.1` 硬编码在 fallback 中 |

---

## 3. 20% Checklist 验证

### FC-1: CSS class undefined
- [x] 错误处理 — N/A（纯前端静态渲染）
- [x] 输入验证 — CSS Modules 作用域隔离
- [x] 日志记录 — N/A
- [x] 向后兼容 — 视觉效果不变
- [x] 安全性 — 无用户输入

### FC-2: NIM reranker 导出
- [x] 错误处理 — `httpx` 依赖在类 `__init__` 中检查
- [x] 输入验证 — N/A（模块级导入）
- [x] 日志记录 — N/A
- [x] 向后兼容 — 纯增量变更
- [x] 安全性 — 无安全风险

### FC-3: OceanBase forget marker
- [x] 错误处理 — 纯数据构造，无异常路径
- [x] 输入验证 — `get_current_datetime()` 返回有效 datetime
- [x] 日志记录 — 保持不变
- [x] 向后兼容 — 保留 top-level 字段
- [x] 安全性 — 无注入风险（ISO 字符串）

### FC-4: retention_score null
- [x] 错误处理 — `.get('current_retention', 1.0)` 默认值
- [x] 输入验证 — 嵌套 `.get()` 安全访问
- [x] 日志记录 — N/A
- [x] 向后兼容 — 字段已存在，仅改变值来源
- [x] 安全性 — 无安全风险

### FC-5: API 异常信息泄露
- [x] 错误处理 — 所有 except 块保留日志记录
- [x] 输入验证 — N/A（异常处理）
- [x] 日志记录 — `logger.error(..., exc_info=True)` 保留
- [x] 向后兼容 — `APIError` 结构不变
- [x] 安全性 — `system.py:246` 遗漏（见上文）

### FC-6: 重要性评估统一
- [x] 错误处理 — `max(0.0, min(1.0, ...))` clamp
- [x] 输入验证 — 各 `_evaluate_*` 方法独立处理
- [x] 日志记录 — N/A
- [x] 向后兼容 — 返回值范围 [0,1] 不变
- [x] 安全性 — 无安全风险

### FC-7: Ebbinghaus 衰减算法
- [x] 错误处理 — `except Exception` fallback
- [x] 输入验证 — `EbbinghausAlgorithm` 内部验证
- [x] 日志记录 — 保持不变
- [x] 向后兼容 — 方法签名和返回值结构不变
- [x] 安全性 — 算法参数使用默认值，不可被外部恶意配置

---

## 4. 总结

### 总体结论: ⚠️ CONDITIONAL APPROVED

### 遗留问题清单

| # | 级别 | 文件 | 描述 | 建议 |
|---|------|------|------|------|
| 1 | **High** | `src/server/api/v1/system.py:246` | `str(e)` 泄露未修复，API 响应暴露原始异常 | 移除 `str(e)`，使用通用消息 `"Failed to delete all memories"` |
| 2 | Minor | `multi_agent.py:26`, `multi_user.py:22` | 未使用的模块级 `from powermem.core.memory import Memory` 导入 | 移除或改用局部导入 |
| 3 | Minor | `multi_agent.py`, `multi_user.py` (FC-7 fallback) | `except Exception` 过于宽泛，可能隐藏编程错误 | 改为 `except (KeyError, TypeError, ValueError)` 或记录 warning |
| 4 | Minor | `multi_agent.py` (FC-7 fallback) | `decay_rate = 0.1` 硬编码魔法数字 | 使用 `self._ebbinghaus.decay_rate` 或配置默认值 |
| 5 | Info | `importance_evaluator.py` | `_evaluate_personal()` 改用词边界匹配，行为变更 | 确认下游无依赖旧子串匹配行为 |

### 放行条件

- **必须修复**: Issue #1（`system.py:246` `str(e)` 泄露）
- **建议修复**: Issue #2-4（代码质量改进）
- **确认即可**: Issue #5（行为变更是改进）
