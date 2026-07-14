- Proposal Name: `advanced_execution_and_integration`
- Start Date: 2026-07-10
- RFC PR: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/pull/2)
- Tracking Issue: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/issues/2)
- Parent RFC: [RFC 0002：Core SDK 产品模型](0002_core_sdk_product_model.md)
- Related Appendix: [类型与接口](0002_appendix_types_and_interfaces.md)

# Status

这份 integration guideline 不具有规范性。它说明 PowerContext 如何接入 Agent harness 和宿主执行环境。
Scheduler、workflow、Graph、Trigger 和 Agent framework 继续使用自己的协议。

# Summary

PowerContext 只拥有持久化 Source、Artifact Revision 和显式血缘。其他运行状态保持在原生所有者中：

| 状态或能力 | 所有者 |
| --- | --- |
| 当前 session 消息、tool state 和即时上下文 | Agent harness |
| 原始材料和 framework-native run state | Source provider 或宿主系统 |
| Source identity、Artifact Revision 和血缘 | PowerContext Catalog |
| 进程内后台提炼 | Core 内部策略 |
| durable schedule、retry、recovery 和 approval | 宿主 scheduler 或 workflow runtime |
| 文件内容和 backend I/O | fsspec 与具体 file-backed Family |

# Recommended integration path

## 模型调用前

Adapter 可以根据当前任务 best-effort 检索 Memory 或其他 Artifact，再通过框架原生 middleware、hook 或
context provider 注入模型上下文：

```python
hits = await pc.memory.search(query, limit=5)
```

PowerContext 不决定 projection 在 prompt 中的优先级、位置或 token budget。Adapter 负责 authorization、redaction、
delimiter 和 prompt-injection 隔离。检索结果是参考上下文，不是更高优先级的 policy。

## 一次 run 完成后

Adapter 把框架原生 run value 交给对应 typed Source provider，然后只需提交 Source：

```python
source_input = await pc.sources.resolve(run)
source = await pc.sources.add(source_input)
```

这一步不要求 adapter 立即调用 `memory.remember()`。当前 session 的基本内容继续由 Agent harness 保留；
PowerContext 的后台策略可在后续聚合 Source 并提炼 Artifact。

## 必须当场生成时

当当前 workflow 必须使用新生成的 Artifact 时，宿主可以显式等待语义操作：

```python
memory = await pc.memory.remember(sources=(source,))
```

这是有明确时序要求的高级路径，不应成为所有 Agent integration 的默认步骤。

# Background processing boundary

`Sources.add()` 只承诺 Source 已持久化。内部策略可以根据累积数量或周期条件发起后续处理，但不提供：

- Source-to-Artifact read-after-write；
- durable counter 或 schedule；
- 跨进程的 exactly-once 执行。

具体阈值、batch selection、目标 Artifact identity、失败重试和可观测性仍是内部实现策略。需要跨进程
恢复、长时间等待、人工确认或可审计 retry 时，该执行生命周期归宿主 scheduler 或 durable workflow runtime
所有。

# Lineage across execution systems

Artifact 血缘只记录某个 Revision 实际使用的 Source 和上游 Artifact Revision。调用方在语义操作前用
`get()` 重建完整对象，并将实际使用的对象传入 Core。

执行系统的 metadata 可以用于 correlation，但不会自动成为 Artifact 血缘。

血缘与 Revision 一起提交，旧 Revision 不会因代码、workflow 或索引变化而被改写。

# Operational guidelines

- Source 以 `(source_type, uri)` 幂等提交；宿主仍应为外部 delivery 做认证、去重和 acknowledgement；
- Artifact revise 使用精确 base Revision；发生 stale conflict 后重新读取并决定是否重新计算；
- nondeterministic generation 的 retry 应由一个明确且有界的所有者控制；
- Engine、filesystem、model client 和 Agent object 在执行进程中构造，不作为 durable job payload；
- framework-native run material 的 retention 和 redaction 归 provider 或宿主所有。
