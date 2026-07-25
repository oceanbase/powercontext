# 对抗审查报告 — powermem issue batch fix

**审查日期**: 2026-07-25
**分支**: fix/issue-batch-2026-07-25
**审查员**: reviewer-dev
**Phase**: 7.5

---

## 1. 安全攻击面

### FC-5: API 异常信息泄露（重点审查）

| 攻击类型 | 文件 | 风险等级 | 描述 | 建议修复 |
|---------|------|---------|------|----------|
| 信息泄露 | `src/server/api/v1/system.py:246` | **High** | `message=f"Failed to delete all memories: {str(e)}"` 仍暴露原始异常。攻击者可通过触发删除操作获取内部错误信息（数据库连接字符串、表结构等） | 替换为 `message="Failed to delete all memories"` |
| 信息泄露 | `src/server/middleware/logging.py:320,330` | **Low** | `error: str(e)` 出现在日志 middleware 的 extra 字段中。不直接暴露给客户端，但若日志被不当暴露（如日志文件可被外部访问），可泄露信息 | 当前可接受，日志不应对外暴露 |
| 信息泄露 | `src/server/main.py:94` | **Low** | `app.state.service_startup_error = str(e)` 存储在应用状态中。若 health check 或调试端点读取此字段，可能泄露 | 确认无端点直接读取此字段 |

**FC-5 总体评估**: 21 处指定泄露点已修复，1 处遗漏（`system.py:246`）。修复模式正确（保留日志、使用通用消息）。

---

### FC-3: OceanBase forget marker — 注入风险

| 攻击类型 | 文件 | 风险等级 | 描述 |
|---------|------|---------|------|
| JSON 注入 | `src/powermem/core/memory.py` | **Low** | `_forget_marker_updates()` 写入的 `should_forget: True` 和 `marked_for_forgetting_at: <ISO string>` 是固定值和受控格式。ISO 时间戳由 `get_current_datetime().isoformat()` 生成，不可被外部输入注入。 |
| Metadata 覆盖 | `src/powermem/core/memory.py` | **Medium** | `updates.update(_forget_marker_updates())` 中新增的 `metadata` key 会覆盖 `updates` 中已有的 `metadata`（如有）。在 `on_get` 触发 `delete_flag` 时，若 `updates` 已有 `metadata`，会被完全覆盖而非合并。 |

**验证**: `metadata` 写入的内容是硬编码的布尔值和 `datetime.isoformat()` 输出，无用户输入，注入风险极低。

---

### FC-7: Ebbinghaus 算法参数

| 攻击类型 | 文件 | 风险等级 | 描述 |
|---------|------|---------|------|
| 配置篡改 | `multi_agent.py`, `multi_user.py` | **Low** | `EbbinghausAlgorithm({})` 使用空 config，所有参数为默认值。攻击者无法通过 API 修改算法参数。 |
| 拒绝服务 | `multi_agent.py`, `multi_user.py` | **Low** | `calculate_current_retention()` 是 O(1) 数学计算，无循环或递归，不会因输入大小导致性能问题。 |
| 数值溢出 | `ebbinghaus_algorithm.py` | **Low** | `math.exp(-t/S)` 中若 `t/S` 极大，`exp()` 返回 0.0（Python float 下溢），不会抛异常。结果被 `max(0.0, min(1.0, ...))` clamp。 |

---

### FC-6: 重要性评估

| 攻击类型 | 文件 | 风险等级 | 描述 |
|---------|------|---------|------|
| 输入操控 | `importance_evaluator.py` | **Low** | 攻击者可通过精心构造的内容（包含大量关键词）使重要性评分偏高。但评分上限为 1.0（clamp），且 `_evaluate_*` 方法使用简单关键词匹配，单个维度最高 ~1.0。 |
| 正则表达式 DoS | `importance_evaluator.py` | **Low** | `_evaluate_personal()` 使用 `re.search(r'\b' + re.escape(indicator) + r'\b', ...)`。`re.escape()` 确保无正则注入，`\b` 是简单锚点，无回溯风险。 |

---

## 2. 竞态条件

| 场景 | 文件 | 风险等级 | 描述 |
|------|------|---------|------|
| 并发 forget 操作 | `core/memory.py` | **Low** | `_forget_marker_updates()` 每次调用生成新的 ISO 时间戳。并发调用不会产生数据竞争，但可能导致同一记忆被多次标记（幂等性由 `should_forget: True` 保证）。 |
| 并发 decay 更新 | `multi_agent.py`, `multi_user.py` | **Low** | `update_memory_decay()` 遍历 `self.scope_memories` / `self.user_memories`。若其他线程同时修改这些 dict，可能产生 `RuntimeError: dictionary changed size during iteration`。但此操作通常在单线程上下文中执行。 |
| `**metadata` 展开覆盖 | `multi_agent.py`, `multi_user.py` (FC-4) | **Medium** | `**memory_data.get('metadata', {})` 展开时，若 `enhanced_metadata` 中有 `retention_score` key，会覆盖前面显式设置的 `retention_score`。当前 `enhanced_metadata` 不包含此 key，但这是隐式假设。 |

---

## 3. 数据泄露

| 场景 | 文件 | 风险等级 | 描述 |
|------|------|---------|------|
| 日志中的异常信息 | `memory_service.py`, `user_service.py`, `agent_service.py`, `search_service.py`, `health_check.py` | **Low** | `logger.error(f"Failed to ...: {e}", exc_info=True)` 记录原始异常到服务器日志。这是预期行为（NFR-5.2），但需确保日志访问权限受控。 |
| 错误消息枚举 | FC-5 通用消息 | **Low** | 通用消息（如 `"Failed to create memory"`）不泄露内部细节，但攻击者可通过不同端点的错误消息推断系统结构。这是可接受的信息泄露级别。 |
| `system.py` 泄露 | `src/server/api/v1/system.py:246` | **High** | `str(e)` 直接暴露在 API 响应中，可能包含数据库连接字符串、表名、列名等敏感信息。 |

---

## 4. 架构一致性

| 检查项 | 状态 | 说明 |
|--------|------|------|
| FC-3 修复模式是否符合架构 | ✅ | 在 `_forget_marker_updates()` 中同时写入 top-level 和 metadata 字段，符合项目的"兼容多存储后端"架构 |
| FC-4 修复模式是否符合架构 | ✅ | 直接从 `enhanced_metadata` 提取数据，符合项目的"数据源优先"模式 |
| FC-5 修复模式是否符合架构 | ✅ | 遵循 `service_errors.py` 的通用消息模式 |
| FC-6 重构是否符合架构 | ✅ | 复用现有 `_evaluate_*` 方法，不引入新依赖 |
| FC-7 替换是否符合架构 | ✅ | 使用现有 `EbbinghausAlgorithm` 类，不重复造轮子 |
| 是否引入不一致模式 | ⚠️ | FC-7 的 `EbbinghausAlgorithm({})` 空 config 初始化与项目其他地方的 config 传递模式不一致 |
| 是否引入循环依赖 | ⚠️ | `multi_agent.py` 和 `multi_user.py` 新增 `from powermem.core.memory import Memory` 模块级导入，但方法内已有局部导入。模块级导入增加了 `agent → core` 的耦合 |

---

## 5. 对抗审查结论

### 问题统计

| 级别 | 数量 | 描述 |
|------|------|------|
| **Critical** | 0 | — |
| **High** | 1 | `system.py:246` `str(e)` 泄露未修复 |
| **Medium** | 2 | `metadata` 覆盖风险（FC-3）、`**metadata` 展开覆盖风险（FC-4） |
| **Low** | 5 | 日志中异常信息、配置篡改、数值溢出、并发迭代、错误消息枚举 |

### 总体评价: ⚠️ CONDITIONAL APPROVE

**放行条件**:
1. **必须修复**: `src/server/api/v1/system.py:246` — 移除 `str(e)`，使用通用消息
2. **建议修复**: 确认 `metadata` 覆盖场景（FC-3 `on_get` 触发 `delete_flag` 时）
3. **建议修复**: 确认 `enhanced_metadata` 不包含顶层 `retention_score` key（FC-4）

**安全评估**: 除 `system.py` 遗漏外，FC-5 的修复模式正确且完整。FC-3、FC-6、FC-7 无显著安全风险。FC-4 的 `**metadata` 展开覆盖是潜在的数据完整性风险，但当前实现中不触发。
