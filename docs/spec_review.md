# Spec 评审报告 — powermem issue batch fix

> **评审者**: reviewer-dev (Phase 2.5)
> **日期**: 2026-07-25
> **输入文件**: `docs/SPEC.md` (856 行, 7 FC), `docs/requirements.md` (7 用户故事, 28 AC)

---

## 1. 总体评估

**Verdict: CONDITIONAL APPROVE**

7 个 FC 整体质量高，AC→FC 映射完整（28/28），file:line 引用经验证准确，NFR 覆盖充分。存在 1 个 High 问题和 3 个 Medium 问题需要关注，但不构成 REJECT 理由。

**关键发现**：
- FC-5（安全漏洞修复）的 21 处 `str(e)` 泄露点全部准确识别，但安全分类深度不足
- FC-7 的 Ebbinghaus 迁移规范清晰，但旧记忆数据格式兼容性需补充说明
- FC-4 的 `**metadata` 展开覆盖风险已被识别但未给出确定性解决方案

---

## 2. 按 FC 逐个评审

### FC-1: Issue #1178 — CSS class undefined

**评估**: ✅ PASS

| 维度 | 结果 | 说明 |
|------|------|------|
| 完整性 | ✅ | AC-1.1~1.3 全部覆盖 |
| 可测试性 | ✅ | 变更前后对比清晰，className 断言可直接编写 |
| 一致性 | ✅ | 无矛盾 |
| 变更可行性 | ✅ | file:line (Features/index.tsx:103) 已验证准确 |

**发现**：无

---

### FC-2: Issue #1158 — NIM reranker 导出

**评估**: ✅ PASS

| 维度 | 结果 | 说明 |
|------|------|------|
| 完整性 | ✅ | AC-2.1~2.4 全部覆盖，含 `import *` 和 `help()` 边界 |
| 可测试性 | ✅ | 导入测试直接可写 |
| 一致性 | ✅ | 无矛盾 |
| 变更可行性 | ✅ | `__init__.py` + `__all__` 变更明确 |

**发现**：无

---

### FC-3: Issue #1151 — OceanBase forget marker

**评估**: ✅ PASS (附 1 个 Medium 建议)

| 维度 | 结果 | 说明 |
|------|------|------|
| 完整性 | ✅ | AC-3.1~3.4 全部覆盖 |
| 可测试性 | ✅ | OceanBase + SQLite 双路径测试明确 |
| 一致性 | ✅ | NFR-3.1 向后兼容已说明 |
| 变更可行性 | ✅ | file:line (memory.py:56) 已验证准确 |

**Medium 建议**：

⚠️ **metadata 覆盖风险**：`updates.update(_forget_marker_updates())` 中新增的 `metadata` 键会覆盖已有 `metadata`。Spec 边界条件中已识别此问题，但未明确说明：如果已有 `metadata` 中存在其他键值（如用户自定义 metadata），这些数据将丢失。建议在行为变更中补充合并策略说明（如 `metadata` 字典的 `update` 语义）。

---

### FC-4: Issue #1143 — retention_score null 修复

**评估**: ✅ PASS (附 1 个 Medium 建议)

| 维度 | 结果 | 说明 |
|------|------|------|
| 完整性 | ✅ | AC-4.1~4.4 全部覆盖，含默认值 1.0 场景 |
| 可测试性 | ✅ | multi-agent/multi-user 双路径 + LLM 未启用场景明确 |
| 一致性 | ✅ | 与 FC-7 Ebbinghaus 算法一致（FC-7 计算 retention，FC-4 确保持久化） |
| 变更可行性 | ✅ | file:line (multi_agent.py:328, multi_user.py:249) 已验证准确 |

**Medium 建议**：

⚠️ **`**metadata` 展开覆盖**：Spec 边界条件中明确指出 `**memory_data.get('metadata', {})` 展开可能覆盖前面的 `retention_score`，并说"需确认 `enhanced_metadata` 中不包含顶层 `retention_score` key"。这种"需确认"的表述在 Spec 中应为确定性结论，而非待确认事项。建议 Coder-Dev 在实现时添加防御性检查或断言。

---

### FC-5: Issue #1137 — API 异常信息泄露修复

**评估**: ✅ PASS (附 1 个 High 问题)

| 维度 | 结果 | 说明 |
|------|------|------|
| 完整性 | ✅ | AC-5.1~5.5 全部覆盖，21 处 `str(e)` 泄露点全部识别并映射 |
| 可测试性 | ⚠️ | 通用消息映射表完整，但缺乏"安全可暴露异常类型"的穷举清单 |
| 一致性 | ✅ | 与 NFR-4 安全约束一致 |
| 变更可行性 | ✅ | 所有 file:line 引用经 `grep` 验证准确 |

**High 问题**：

🔴 **安全异常分类不够详细**：Spec 提到"需区分安全可暴露的错误（如 `ValidationError`）和敏感的内部异常"，但仅举了 `ValidationError` 一个例子。对于 21 处修改点，Coder-Dev 需要逐个判断异常类型。建议补充：
1. **可安全暴露的异常类型白名单**：`ValidationError`, `ValueError`, `KeyError`（字段名级别）等
2. **绝对不可暴露的异常类型黑名单**：`ConnectionError`, `OSError`, `sqlite3.OperationalError` 等包含连接字符串的异常
3. **各 service 方法的预期异常类型**：如 `memory_service.create_memory()` 可能抛出 `ValidationError`（安全）或 `IntegrityError`（不安全）

此外，测试策略中说"触发异常后验证响应中 `message` 不包含 `str(e)` 内容"，但未说明如何穷举触发所有 21 处异常路径。

---

### FC-6: Issue #1141 — 重要性评估统一

**评估**: ✅ PASS

| 维度 | 结果 | 说明 |
|------|------|------|
| 完整性 | ✅ | AC-6.1~6.5 全部覆盖，含零输入场景 |
| 可测试性 | ✅ | 加权和计算可直接断言，`get_importance_breakdown()` 的 `weighted_total` 可验证 |
| 一致性 | ✅ | 与 FC-7 的 `importance_score` 使用保持一致 |
| 变更可行性 | ✅ | 单文件变更，`importance_evaluator.py` 存在 |

**发现**：无

---

### FC-7: Issue #1149 — Ebbinghaus 衰减算法

**评估**: ✅ PASS (附 1 个 Medium 建议)

| 维度 | 结果 | 说明 |
|------|------|------|
| 完整性 | ✅ | AC-7.1~7.6 全部覆盖，含 fallback 和跨模块一致性 |
| 可测试性 | ✅ | 衰减速度对比（working vs long_term）、强化因子效果可测试 |
| 一致性 | ✅ | 与 FC-4 的 `retention_score` 持久化一致 |
| 变更可行性 | ⚠️ | multi_user.py 的 `update_memory_decay()` 行号为 901，Spec 标注 ~919，偏差 18 行 |

**Medium 建议**：

⚠️ **旧记忆数据格式兼容**：`EbbinghausAlgorithm.calculate_current_retention()` 需要 `metadata.intelligence` 结构。现有数据库中已存储的记忆可能没有此结构（尤其是 FC-4 修复前存储的 `retention_score: null` 的记忆）。Spec 的边界条件提到"agent memory 的数据格式需兼容此结构"，但未明确旧数据的迁移策略。建议补充：当 `metadata.intelligence` 不存在时，`EbbinghausAlgorithm` 应如何处理（使用 `retention_score` 作为 `current_retention` 的 fallback？使用默认值？）。

---

## 3. NFR 覆盖检查

| NFR | 描述 | 覆盖 FC | 评估 | 说明 |
|-----|------|---------|------|------|
| NFR-1 | 向后兼容 | FC-1~7 | ✅ | 每个 FC 均有向后兼容说明 |
| NFR-2 | 最小变更 | FC-1~4 | ✅ | 变更范围明确，FC-1 仅 1 行 |
| NFR-3 | 代码质量 | FC-2,6,7 | ✅ | 无新 lint 警告的要求明确 |
| NFR-4 | 安全 | FC-5 | ⚠️ | 覆盖但深度不足（见 FC-5 High 问题） |
| NFR-5 | 可观测性 | FC-5,7 | ✅ | 日志保留要求明确 |
| NFR-6 | 数据完整性 | FC-3,4 | ✅ | 遗忘标记 + retention_score 不为 null |
| NFR-7 | 算法一致性 | FC-6,7 | ✅ | 规则引擎与 LLM 引擎使用相同框架 |

**遗漏的 NFR**：
- 无 NFR 覆盖 **性能影响**：FC-7 的 Ebbinghaus 计算在 `update_memory_decay()` 中遍历所有记忆，每个记忆调用 `calculate_current_retention()`。虽然单次计算是 O(1)，但大量记忆时的批量性能未评估。
- 无 NFR 覆盖 **数据迁移**：FC-3 和 FC-4 修复后，已存储的旧记忆数据格式不兼容新逻辑。

---

## 4. 风险项

| 风险 | 级别 | FC | 描述 | 缓解建议 |
|------|------|-----|------|----------|
| 安全分类不完整 | High | FC-5 | 21 处修改缺乏异常类型白/黑名单，Coder-Dev 需逐个判断 | 补充安全异常分类表 |
| 旧数据格式不兼容 | Medium | FC-7 | 数据库中已存储的记忆缺少 `metadata.intelligence` 结构 | 补充旧数据 fallback 策略 |
| metadata 覆盖 | Medium | FC-3 | `updates.update()` 可能覆盖已有 metadata 键 | 明确合并策略 |
| `**metadata` 展开覆盖 | Medium | FC-4 | 展开可能覆盖 `retention_score` | 添加防御性断言 |
| multi_user.py 行号偏差 | Low | FC-7 | Spec 标注 ~919，实际 901 | Coder-Dev 以实际代码为准 |

---

## 5. 关键发现

1. **28/28 AC 完整映射**：所有验收标准均有对应 FC 覆盖，无遗漏。
2. **file:line 引用准确**：经 `grep` 验证，所有源码位置引用正确（FC-7 multi_user.py 有 18 行偏差，使用 `~` 标记可接受）。
3. **FC-5 安全修复规范最详尽**：21 处泄露点全部列出通用消息映射表，是 7 个 FC 中最完整的。
4. **FC-5 也是风险最高的**：涉及 4 个文件 21 处修改，安全异常分类需补充。
5. **FC-3 和 FC-4 的 metadata 操作需谨慎**：两者都涉及 metadata dict 的修改，存在覆盖风险。
6. **FC-6 和 FC-7 的关系清晰**：FC-6 统一评估框架，FC-7 统一衰减算法，两者通过 `importance_score` 和 `retention_score` 关联。
7. **测试策略覆盖充分**：每个 FC 均有测试场景，但 FC-5 的异常穷举测试需要额外设计。

---

## 6. 改进建议汇总

| # | 级别 | FC | 建议 |
|---|------|-----|------|
| 1 | High | FC-5 | 补充安全异常类型白名单/黑名单和各 service 的预期异常类型 |
| 2 | Medium | FC-7 | 补充旧记忆（无 `metadata.intelligence`）的 fallback 处理策略 |
| 3 | Medium | FC-3 | 补充 metadata dict 合并策略说明（`update` 语义 vs 深度合并） |
| 4 | Medium | FC-4 | 将"需确认 `enhanced_metadata` 中不包含顶层 `retention_score`"改为确定性结论 |
| 5 | Low | NFR | 补充性能影响 NFR（大批量记忆的衰减计算）和数据迁移 NFR |

---

**评审完成。Verdict: CONDITIONAL APPROVE — 修复 High 问题（#1）后可放行。**
