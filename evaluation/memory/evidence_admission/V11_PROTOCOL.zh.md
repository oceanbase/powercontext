# Source 窗口对照的协议预算合同

本合同只改变评估层的 `window-reduction-v1` 计划方式。生成指令、schema、绑定编码与评分沿用
[v10 合同](V10_PROTOCOL.zh.md)；候选策略、三套冻结数据、事实标注和预算均不变。
没有线上入口，不读取凭据，不发布 Memory 或生产消费游标。

## 预算与窗口

每次从真实 Scope 的 Source journal 读取连续窗口，经真实投影构造 `AtomicMemoryExtractionInput`。
同一窗口必须同时满足冻结的原生选择成本和完整 v10 协议成本；后者包含指令、schema 与实际请求包装。
成本仍为模型无关估算，不能替代 provider usage 或账单。

超预算时通过真实 `MemorySourceWindowRepository.reduce()` 持久化缩减提示，由
`SourceWindowTrigger` 在同一消费位置将 Source 数减半后重读。窗口内 Source 全部保留，不能截断字段、
拆分 Source、改写 Unicode 或把两个历史窗口重装箱。单 Source 仍超预算则明确拒绝。
此前已经准入的窗口保留，并分别披露后续遗漏；保留部分窗口不表示整个样本准备成功。

单样本上限为 16 次计划调用，累计估算输入不超过该档预算的 8 倍。资源上限拒绝同样保留已准入前缀。
消费位置仅存在于评估内存；恢复提示的本地测试不代表 provider 自动续跑、断电恢复或远端取消。
Scope 隔离、取消传播和零 Memory/生产游标写入均有行为回归。

## 历史证据与代码身份

v10 比较文件的原始 SHA-256 固定为
`273a2571a29475ae629af242efcd839581726e6c4f4e9098a499e33037282728`，
并核验其完整产物文件哈希、样本身份，以及 v9/v8/v7 依赖、清单和结果哈希。
历史内容身份仅在含 `suite_code_hash` 与 `runtime_code_hash` 的代码身份记录中剥离
`revision` 和 `frozen_committed` 两项执行元数据；源码、运行时代码、协议、数据和配置哈希仍须精确相同。
合同中的版本字段不剥离。历史文件本身保持原字节，不重新认证旧运行的提交状态。

本次运行单独记录 Git HEAD，并要求开始与结束一致；另核验内容身份在执行期间未改变。
这避免把一次内容未变的提交误判为历史内容变化，也不把历史提交状态作为新运行授权。
离线产物明确记录 `committed_freeze_verified=false`，下一轮线上执行仍须先提交冻结并重新预检。
模拟提交元数据的测试不能证明已经完成真实提交冻结。

## 比较范围与复放

完整比较包含 1620 个身份：重新执行 324 个窗口样本，其中 279 个经过新预算计划，45 个故障/取消样本
重跑既有边界；其他四个 arm 的 1296 个样本按固定哈希和身份校验后原样复用。
不能将此表述为重新执行全部 1620 个样本，也不能将准备成功当作模型输出保留成功。
检查六个历史协议预算拒绝全部恢复、事实/字面量/请求内联合支持没有回退、三次重放确定。

在仓库根目录执行，输出目录必须不存在；历史 v9 依赖目录须完整可读：

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v11 \
  --baseline-dir evaluation/memory/evidence_admission/results/v10-offline \
  --v9-root .artifacts/evidence-admission/v9-offline \
  --output-dir .artifacts/evidence-admission/v11-minimal --minimal
uv run --project evaluation python -m powercontext_eval_evidence_admission.v11 \
  --baseline-dir evaluation/memory/evidence_admission/results/v10-offline \
  --v9-root .artifacts/evidence-admission/v9-offline \
  --output-dir .artifacts/evidence-admission/v11-comparison
```

脱敏比较包含安全样例标签、范围身份哈希、成本与计数，不包含 Source 正文、prompt、模型响应或凭据。
`COMPLETE.json` 仅表示该本地产物完整；总体结论仍为 `insufficient_evidence`，不授权线上或生产修改。
结果及剩余验收差异见 [v11 结果](V11_RESULTS.zh.md)与[验收对照](ACCEPTANCE.zh.md)。
