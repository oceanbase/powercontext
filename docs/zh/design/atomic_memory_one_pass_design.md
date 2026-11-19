# 系统设计：原子记忆单次整理（One-pass）

| 项 | 内容 |
| --- | --- |
| 特性类型 | 原子记忆的可选处理模式，**默认关闭** |
| 对外契约 | 不新增 HTTP API，不改 OpenAPI，不改持久化格式，不新增提示词 key |
| 新增配置 | 一个 Server 运行时配置项，选择原子记忆处理模式 |
| 文档状态 | 已实现，评测进行中 |
| Language | 中文（技术术语保留英文原文） |

---

## 0. 一句话概括

原子记忆现在的处理方式是：先对 Source 窗口抽取候选，再对每条候选单独召回相关记忆、单独调用模型对账。
单次整理把这两件事放进同一段对话的两步里：第 1 步列出证据里的事实和检索词，Core 按检索词召回已有记忆，
第 2 步在同一对话里直接给出最终的 create / revise / merge / noop 操作。第 2 步复用第 1 步的输入前缀，
可以命中模型服务的前缀缓存，因此不再为每条候选重复发送整段 Source。

它作为可选模式提供：成本明显更低，对话类内容的召回准确率持平，但编码类内容的实质事实覆盖度有可测量的回退
（见 §6）。默认模式不变；在重复配对评测中稳定反超后，再讨论是否替换默认模式。

## 1. 约束

| # | 约束 | 保证方式 |
| --- | --- | --- |
| C1 | 发布路径不变：写入仍经过 `AtomicMemoryWindowWorkset`、写入闸门、`_publish` | 单次整理只替换 `_prepare` 内的候选生成与对账，产出同一个 workset |
| C2 | 溯源不变：每条发布的记忆仍带正确的 `sources` | 第 2 步的每个操作给出 `evidence_ids`，Core 校验后按现有方式计算 `sources` |
| C3 | 校验与读依赖不变 | 每个 revise / merge 转换为 `AtomicMemoryReconciliationOutput` 后由 `workset.apply()` 校验；展示过的已有记忆全部记入 workset 的读依赖，发布时照常做冲突检查 |
| C4 | 提示词配置不分裂：用户仍只维护 `atomic_memory.extract` 与 `atomic_memory.reconcile` | 流程指令是代码常量；两个 key 的现有配置嵌入其中（§4） |
| C5 | 默认行为字节级不变 | 未开启时不进入任何新代码路径 |

## 2. 现有流程（master）

`AtomicMemorySourceWindowProcessor._prepare`（`builtin/runtime/atomic_memory_processing.py`）：

1. 把窗口内可生成的 Source 投影为 `AtomicMemoryEvidence`，`evidence_id` 为 `source:{journal_position}`。
2. `pipeline.extract(AtomicMemoryExtractionInput)` 抽取候选，每条候选至少引用一个 `evidence_id`。
3. 对每条候选：与窗口内已加入 workset 的条目比较，再按 `related_page_size` 分页召回已有记忆；
   每批最多 `comparison_batch_size` 个相关条目，调用一次 `pipeline.reconciler.generate()`，
   用 `workset.apply()` 应用 create / revise / merge / noop。
4. 返回 workset，由 `_inspect_decisions` 与 `_publish` 完成写入闸门与发布。

成本集中在第 3 步：每次对账都重新发送候选证据和相关条目，候选正文在抽取与对账中各输出一次。

## 3. 单次整理流程

### 3.1 对话的三步

```
第 1 步  [流程指令 + 抽取规则] + [证据] ──▶ 模型 ──▶ {"items":[{"fact","query"}, ...]}   (≤30 条)
         Core 用每个 query 召回已有记忆，分配本轮标签 M1, M2, ...
第 2 步  同一前缀 + 第 1 步回复 + [已有记忆列表] ──▶ 模型 ──▶ {"operations":[...]}
第 3 步  仅当某条 create 的正文召回到第 2 步没展示过的记忆时：
         同一前缀 + 第 2 步回复 + [这些 create 与各自的未展示记忆] ──▶ 模型 ──▶ {"operations":[...]}
应用     每一步的操作经 workset.apply() 进入现有发布路径
```

- **第 1 步只负责检索。** 指令明确说明：第 1 步只是为了找出需要比对的已有记忆，拿不准的事实也要列上，
  保留与否由第 2 步决定。评测中去掉这一说明后，模型会随机返回空列表，整轮不写入（§6.3）。
- **第 1 步为空时仍进入第 2 步**，此时展示的已有记忆为空。代价是一次短调用，避免第 1 步误判导致整轮漏写。
- **后一步续接前一步。** 每一步的请求都是上一步的请求加上模型回复和一条新消息，指令与此前的消息逐字相同，
  可由模型服务的前缀缓存命中。这一点不依赖网关。
- **标签而非 ID。** 已有记忆以 M1、M2… 展示，Core 负责映射回 workset key。
- **回复不合规则时在同一步内纠正。** 每一步的回复先应用到 workset 的副本上；标签未知、缺少证据、
  时间限定词丢失等规则被违反时，规则名称返回给模型重答（次数受现有 `max_requests` 限制），副本丢弃。
  纠正次数用尽后本窗口失败，不发布部分结果。

### 3.2 证据与溯源

第 1 步的证据沿用 `AtomicMemoryExtractionInput` 的渲染方式（含输入编码），每段证据带 `evidence_id`。
第 2 步的 create / revise / merge 给出 `evidence_ids`，Core 校验其属于本窗口证据，再按现有方式构造工作条目：
`evidence=selected`、`sources=tuple(item.source_ref for item in selected)`。缺省时：

- 窗口只有一个 Source：补为该 Source；
- 只合并已有记忆、不含新事实的 merge：以被合并记忆的已发布内容作为依据；
- 其余情况按无效回复处理。

create 的内容按抽取候选的同一套规则校验（`validate_extraction_output`：kind、正文、引用的证据、时间限定词）。

### 3.3 应用到 workset

| 操作 | workset 处理 |
| --- | --- |
| create | 以操作内容新建条目，直接保留 |
| revise | 以操作内容新建 proposal，`related` 为唯一目标条目，按 `revise` 应用 |
| merge | 以操作内容新建 proposal，`related` 为全部目标条目，按 `merge` 应用 |
| noop | 不新增条目，目标记忆不变 |

revise / merge 的 `compared_ids` 与 `target_ids` 都取目标条目，`reason` 由模型给出；
`validate_reconciliation_decision` 的规则原样生效。内容与目标完全相同的 revise
（`RedundantAtomicMemoryRevisionError`）按 noop 处理。

### 3.4 复核（第 3 步）

第 2 步里，一条 create 只和它的检索词召回的记忆比过。第 2 步结束后，Core 用每条 create 的正文再召回一次
（同样取前 5 条），找出第 2 步没有展示过的记忆。都没有则不调用；有则在同一对话里追加第 3 步：
列出这些 create（标签 C1、C2…）和各自的未展示记忆，模型对每条 create 重新给出操作
（create 表示保留，revise / merge 与目标合并，noop 表示目标已包含该事实）。这一步与逐候选流程的一次比对等价，
直接用 `workset.apply(候选, 未展示记忆, 操作)` 应用。

### 3.5 比对范围

现有流程通过 `_recall_related_items` 分页遍历候选的全部相关记忆，比对是穷举的。单次整理每个检索词只取前若干条
召回结果（评测值为 5 条，总计 40 条），未进入前列的相关记忆只能由复核补上。这是两种模式在比对范围上的本质差异，
也可能是编码场景覆盖度回退的来源之一（§7）。

## 4. 提示词

流程指令是 `builtin/artifacts/atomic_memory/one_pass.py` 中的代码常量，用户不能修改，相当于 invariant。
其中两处嵌入现有提示词配置：

| 位置 | 来源 |
| --- | --- |
| "什么值得记" | `atomic_memory.extract` 经 `resolve()` 编译后的指令：内置抽取规则（随 profile 变化）；Scope 自定义时还包含其 guidance 与 demonstrations |
| 第 2、3 步的对账原则 | `atomic_memory.reconcile` 的 Scope 自定义指令原文，追加在流程指令之后；未自定义时不追加 |

- 流程指令保持领域中立，不提编码或对话；领域差异全部来自 profile 与 Scope guidance。
- 抽取规则原本面向"输出候选列表"的回复。流程指令在其后说明：选择、措辞、语言与时间方面的规则照常适用，
  输出格式以各步的说明为准。
- `atomic_memory.reconcile` 的内置指令描述的是逐候选回复（`compared_ids` 等），不嵌入；它的 demonstrations
  同样是逐候选格式，单次整理不使用。
- 两个 key 的自定义 Prompt Artifact 都记入本窗口发布条目的 `artifacts`，与逐候选流程一致。
- 带 demonstrations 的 Scope 尚未评测（§7）。

## 5. 配置、预算与失败处理

### 5.1 配置

`RuntimeConfig` 新增 `atomic_memory_processing_mode: Literal["per_candidate", "one_pass"] = "per_candidate"`，
对应环境变量 `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PROCESSING_MODE`，中英文配置文档已同步。
`AtomicMemoryProcessingConfig.from_runtime()` 读取该值。第 1 步事实上限（30）、每个检索词展示的记忆数（5）
与展示总数上限（40）是 `one_pass.py` 中的常量。

注入自定义 `AtomicMemoryGenerationPipeline` 的部署需要同时提供 `conversation` 生成器才能开启该模式，
否则处理窗口时报 `InferenceUnavailableError`。

### 5.2 预算

所有估算沿用 `atomic_memory_input_tokens_limit` 与管线的 token estimator，估算内容为流程指令加上对话中已发送的
全部消息。

- 第 1 步或第 2 步的输入超出预算时，本窗口改用逐候选流程（§2）处理，结果与默认模式相同。
  第 2 步不拆成多次调用，召回到的已有记忆也不因预算丢弃：逐候选流程在预算不足时本来就会把每次比对的记忆数
  减半直到一条，把第 2 步拆到最细也是同样的调用方式，却要另外处理跨批次的相似条目。回到逐候选流程不引入新的
  行为，代价是该窗口没有成本收益，第 2 步超出时还多付一次第 1 步调用。
- 已有记忆列表最多 40 条。只有窗口证据本身接近预算上限时第 2 步才会超出，这类窗口的占比见 §7。
- 第 3 步超出预算时把待复核的 create 对半拆成多次调用，每次都续接第 2 步的回复。各条 create 的复核只针对
  各自的未展示记忆，互不依赖。单条 create 的复核仍超出时按现有的预算错误处理（触发窗口缩减）。

### 5.3 失败与重试

- 任一步输出无效：本窗口按现有 `InvalidInferenceOutputError` 处理，不发布部分结果。
- 推理超时：沿用现有窗口缩减与重试逻辑。
- 并发冲突：沿用 `_retry_window` 的三次重试。整窗重试会重新执行两步对话，与现有流程重新抽取的语义一致。

### 5.4 推理接口

现有 `StructuredGenerator` 的实现把输出 schema 写进指令，不同输出格式的两次调用因此没有相同的前缀。
单次整理使用新增的 `PydanticAIConversationGenerator`：输入是 `ConversationTurn`（指令、新消息、此前的对话、
回复检查函数），回复是纯文本，由检查函数解析并校验，返回 `ConversationReply`（检查结果与可续接的对话）。
它与现有生成器共用同一套超时、请求次数限制、错误映射和用量上报（`UsageReportingStructuredGenerator`），
在链路中的名称为 `atomic_memory_one_pass`。不新增日志字段，不记录提示词与模型输出内容。

## 6. 评测

### 6.1 方法

- **编码场景**：三个仓库共 15 个连续编码轮次，重复 5 次；每次从空 Scope 开始按顺序写入。
  由 gpt-5.6-sol（high reasoning）盲评最终存储，依据 61 条参考事实（其中 18 条为验证类，其余为实质事实），
  标注每条记忆是否有用、过时、错误、重复，以及每条参考事实是否被覆盖。
- **对话场景**：LoCoMo 全部 10 段对话，1540 道问题。按会话顺序写入，以 Core 检索的前 10 条记忆作答，
  答题与判分均使用 gpt-5.6-luna。
- 两种模式使用相同的抽取规则（内置 profile，无 Scope guidance）。单次整理在评测中以对话形式输入证据，
  模型调用经 CLIProxyAPI，检索使用 Core 的 `/v1/memory/search`。

### 6.2 结果

**编码场景**（5 次重复合计，实质事实 43 条 × 5 = 215）

| 模式 | 条目 | 信噪比 | 过时 | 重复 | 实质缺失 |
| --- | --- | --- | --- | --- | --- |
| 现有流程 | 78 | 64% | 22 | 7 | 5.1% |
| 单次整理 | 166 | 79% | 30 | 12 | 9.8% |

按"同一次重复、同一条事实"配对：只有单次整理漏掉的 11 条，只有现有流程漏掉的 1 条（符号检验 p≈0.006），
5 次重复中单次整理均漏得更多。这是确定的覆盖度回退。

**对话场景**（LoCoMo，1540 题）

| 模式 | J | 单跳 | 多跳 | 开放域 | 时间 | 存储条数 |
| --- | --- | --- | --- | --- | --- | --- |
| 现有流程 | 0.797 | 0.716 | 0.819 | 0.604 | 0.838 | 1065 |
| 单次整理 | 0.801 | 0.691 | 0.854 | 0.573 | 0.843 | 2190 |

逐题配对：只有现有流程答对 112 题，只有单次整理答对 117 题，持平。

**成本**（成本单位：未缓存输入 1，缓存输入 0.1，输出 8）

| 场景 | 现有流程 | 单次整理（含逐条复核） | 单次整理（不含复核） |
| --- | --- | --- | --- |
| LoCoMo 10 段对话 | 33.3M | 15.6M | 8.2M |

编码场景中，逐条复核占单次整理成本的 12%，且没有改变任何决定；LoCoMo 中占 48%，改变了 4% 的决定。
§3.4 的批量复核把它合并为每轮尽量少的调用，多数轮次只需一次或不需要。

### 6.3 评估过的备选方案

| 方案 | 结果 | 结论 |
| --- | --- | --- |
| 第 1 步只输出不超过 8 个主题检索词 | 编码实质缺失 16.3%，LoCoMo 0.739 | 同一主题下的多个事实被合并或遗漏，不采用 |
| 按事实列出，但不说明第 1 步只负责检索 | 同一前缀重复 4 次，有 0–2 次返回空列表；编码实质缺失 25.1% | 空列表导致整轮不写入，必须加说明 |
| 第 2 步展示更多已有记忆，并追加"新状态必须修订旧条目"等规则 | 操作分布几乎不变；LoCoMo 降至 0.782（配对 80:109） | 不采用 |
| 在 CODING profile 中要求不记录单次检查结果 | 过时条目 30→16，但实质缺失升至 15.3% | 以覆盖度换信噪比，不采用 |

过时条目主要是"某某测试通过、共 N 个"之类的时间点快照，现有流程同样存在（22 条），不在本设计中处理。

## 7. 待验证项

以下内容在实现原型中验证，结果更新到本节：

1. **Core 证据格式的输入。** 评测以对话形式输入证据；Core 的实现以带 `evidence_id` 的证据渲染输入，
   且一个窗口可包含多个 Source。需要在原型上重跑两个数据集，确认覆盖度与 J 不变，并确认 `evidence_ids` 的正确率。
2. **批量复核。** 确认合并为一次调用后，纠正率不低于逐条复核。
3. **Scope demonstrations。** 用带 demonstrations 的 Scope 验证第 2 步输出格式不受影响。
4. **窗口包含多个 Source 时的事实上限。** 评测中每轮只有一个 Source；多个 Source 的窗口可能触达 30 条上限。
5. **比对范围。** 测量放宽每个检索词的召回条数（或按 `related_page_size` 分页展示）对编码场景覆盖度和成本的影响，
   确认回退中有多少来自 §3.5 的比对范围差异。
6. **第 2 步超出预算的窗口占比。** 在真实 Source 上统计改用逐候选流程的窗口比例；占比高到明显抵消成本收益时，
   再考虑在切分窗口时为已有记忆列表预留预算。

## 8. 替换默认模式的条件

同时满足以下条件后，再讨论把单次整理设为默认：

- 编码场景在重复配对评测中，实质缺失不高于现有流程（配对差异不显著或单次整理更优）；
- LoCoMo 的 J 不低于现有流程；
- 成本优势保持。

## 附录 A：流程指令

全文见 `src/powercontext/builtin/artifacts/atomic_memory/one_pass.py` 的 `_INSTRUCTIONS`。结构如下：

1. 说明输入：第一条消息是带 `evidence_id` 的证据 JSON，已有记忆在后续消息里给出，且不能作为新事实的证据。
2. "什么值得记"：嵌入 `atomic_memory.extract` 的编译结果。
3. 第 1 步：逐条列出事实与检索词，至多 30 条；拿不准也列上。
4. 第 2 步：create / revise / merge / noop 的定义、同一事实优先修订的原则、每条记忆的写法、输出格式。
5. 第 3 步：对列出的 create 重新决定。
