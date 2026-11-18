# 系统设计：原子记忆单次整理（One-pass）

| 项 | 内容 |
| --- | --- |
| 特性类型 | 原子记忆的可选处理模式，**默认关闭** |
| 对外契约 | 不新增 HTTP API，不改 OpenAPI，不改持久化格式，不新增提示词 key |
| 新增配置 | 一个 Server 运行时配置项，选择原子记忆处理模式 |
| 文档状态 | 设计评审 |
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
| C3 | 审计不变：记录比较过哪些已有记忆、使用了哪些提示词 | 每个操作按现有 `AtomicMemoryReconciliationOutput` 校验，`compared_ids` 取本轮展示过的相关条目 |
| C4 | 提示词配置不分裂：用户仍只维护 `atomic_memory.extract` 与 `atomic_memory.reconcile` | 两步流程本身是带版本号的代码常量；Scope guidance 按现有 `resolve()` 编译后嵌入 |
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

### 3.1 两步对话

```
第 1 步  [流程指令 + 抽取规则 + 证据] ──▶ 模型 ──▶ {"items":[{"fact","query"}, ...]}   (≤30 条)
         Core 用每个 query 召回已有记忆，分配本轮标签 M1, M2, ...
第 2 步  [同一前缀 + 第 1 步回复 + 已有记忆列表] ──▶ 模型 ──▶ {"operations":[...]}
复核     对 create 收集未展示过的相似记忆；没有则跳过，有则在预算内合并复核
应用     每个操作转换为 AtomicMemoryReconciliationOutput，经 workset.apply() 进入现有发布路径
```

- **第 1 步只负责检索。** 指令明确说明：第 1 步只是为了找出需要比对的已有记忆，拿不准的事实也要列上，
  保留与否由第 2 步决定。评测中去掉这一说明后，模型会随机返回空列表，整轮不写入（§6.3）。
- **第 1 步为空时仍进入第 2 步**，此时展示的已有记忆为空。代价是一次短调用，避免第 1 步误判导致整轮漏写。
- **第 2 步续接第 1 步。** 第 2 步的请求是第 1 步请求加上第 1 步回复和一条新消息，前缀完全相同，
  可由模型服务的前缀缓存命中。这一点不依赖网关。
- **标签而非 ID。** 已有记忆以 M1、M2… 展示，Core 负责映射回 workset key，模型输出的未知标签按无效输出处理。

### 3.2 证据与溯源

第 1 步的证据沿用 `AtomicMemoryExtractionInput` 的渲染方式，每段证据带 `evidence_id`。第 2 步的
create / revise / merge 必须给出 `evidence_ids`，Core 校验其属于本窗口证据，再按现有方式构造工作条目：
`evidence=selected`、`sources=tuple(item.source_ref for item in selected)`。noop 的 `evidence_ids` 为空。
窗口只有一个 Source 时，缺省 `evidence_ids` 可由 Core 直接补为该 Source。

### 3.3 应用到 workset

| 操作 | workset 处理 |
| --- | --- |
| create | 以操作内容新建 proposal 条目，`related` 为空，按 `create` 应用 |
| revise | 以操作内容新建 proposal，`related` 为唯一目标条目，按 `revise` 应用 |
| merge | 以操作内容新建 proposal，`related` 为全部目标条目，按 `merge` 应用 |
| noop | 不新增条目，记录 `compared_ids` 与 `reason` 供审计 |

`compared_ids` 取第 2 步展示过、且与该操作的事实相关的条目；`reason` 由第 2 步一并输出。
现有 `RedundantAtomicMemoryRevisionError` 等校验规则原样生效。

### 3.4 批量复核

第 2 步结束后，Core 用每条 create 的正文召回相关记忆，收集第 2 步没有展示过的条目。若不存在则不调用；
若存在，把待复核的 create 及其未展示过的相关记忆合并成尽量少的复核调用（按 §5.2 的预算分批），模型对每条
create 重新给出操作。复核同样续接第 2 步的前缀。

### 3.5 比对范围

现有流程通过 `_recall_related_items` 分页遍历候选的全部相关记忆，比对是穷举的。单次整理每个检索词只取前若干条
召回结果（评测值为 5 条，总计 40 条），未进入前列的相关记忆只能由复核补上。这是两种模式在比对范围上的本质差异，
也可能是编码场景覆盖度回退的来源之一（§7）。

## 4. 提示词

两步流程的指令是代码常量，带版本号（例如 `powercontext.atomic-memory.one-pass.v1`），用户不能修改，
相当于 invariant。其中两处嵌入现有提示词配置：

| 位置 | 来源 |
| --- | --- |
| "什么值得记" | `atomic_memory.extract` 经 `resolve()` 编译后的指令：内置抽取规则（随 profile 变化），以及 Scope 的 custom guidance |
| 第 2 步的对账原则 | `atomic_memory.reconcile` 的 Scope custom guidance，追加在操作定义之后 |

- 流程指令保持领域中立，不提编码或对话；领域差异全部来自 profile 与 Scope guidance。
- 提示词 digest 与 builtin_version 需要包含单次整理的流程版本号，避免两种模式被当作同一份提示词审计或缓存。
- Scope 的 demonstrations 以"输入 → 候选"的形式校验。嵌入单次整理时只作为"哪些内容值得记住"的示例渲染，
  不带输出格式，以免干扰第 2 步的操作格式。这一路径尚未评测（§7）。

附录 A 给出评测所用的流程指令全文。

## 5. 配置、预算与失败处理

### 5.1 配置

`RuntimeConfig` 新增 `atomic_memory_processing_mode: Literal["per_candidate", "one_pass"] = "per_candidate"`，
对应环境变量 `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PROCESSING_MODE`，同步写入中英文配置文档。
`AtomicMemoryProcessingConfig.from_runtime()` 读取该值。第 1 步事实上限与展示的已有记忆上限作为内部常量，
评测值分别为 30 和每个 query 5 条、总计 40 条。

### 5.2 预算

- 第 1 步输入与现有抽取相同，沿用 `atomic_memory.extract` 的预算检查；超出时沿用窗口缩减。
- 第 2 步输入为第 1 步输入加上已有记忆列表，按 `atomic_memory_input_tokens_limit` 检查。召回到的已有记忆
  全部展示，不因预算丢弃：超出时把第 2 步拆成多次调用，每次都续接"第 1 步输入 + 第 1 步回复"这一前缀。
  分批遵循以下规则，避免不同批次写出相似的记忆：
  1. 每个事实只属于一批，每批的指令只要求为分到的事实给出操作。
  2. 召回到同一条已有记忆的事实分在同一批（按共享召回结果做并查集分组，以组为单位分批），同一条已有记忆的
     revise / merge / noop 因此只在一次调用里决定。
  3. 前几批 create 或 revise 得到的条目以单独标签（W1, W2, ...）展示给后续批次，后续批次可以对它们
     noop、revise 或 merge；这与现有流程中后续候选先与窗口内条目比较（`_window_comparisons`）一致。
- 复核调用按同样的规则分批，并且除未展示过的已有记忆外，也比对本轮其他批次创建的相似条目。
- Core 的证据输入本身受同一预算约束，窗口接近上限时第 2 步更容易超出预算，分批在 Core 中可能比评测中常见。
- 单个事实召回的记忆本身就超出预算时，沿用现有的窗口缩减。

### 5.3 失败与重试

- 任一步输出无效：本窗口按现有 `InvalidInferenceOutputError` 处理，不发布部分结果。
- 推理超时：沿用现有窗口缩减与重试逻辑。
- 并发冲突：沿用 `_retry_window` 的三次重试。整窗重试会重新执行两步对话，与现有流程重新抽取的语义一致。

### 5.4 推理接口

现有 `StructuredGenerator` 是单次调用。单次整理需要在同一对话里连续生成：管线新增一个支持
消息历史的生成接口，由 Pydantic AI 的 `message_history` 实现，第 2 步与复核分别传入前序消息。
观测沿用现有 stage 与 span，按步骤区分 operation，不记录提示词与模型输出内容。

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
6. **第 2 步分批。** 用接近预算上限的窗口触发分批，确认分批后的相似条目数与不分批时相当。

## 8. 替换默认模式的条件

同时满足以下条件后，再讨论把单次整理设为默认：

- 编码场景在重复配对评测中，实质缺失不高于现有流程（配对差异不显著或单次整理更优）；
- LoCoMo 的 J 不低于现有流程；
- 成本优势保持。

## 附录 A：流程指令（评测版本）

`{extraction_rules}` 处嵌入 `atomic_memory.extract` 的编译结果；`{max_items}` 为 30。

```text
You are now curating long-term memory for this session. Nothing below is a task for you to work on.

The conversation above is the source. Only its most recent user turn is evidence: the last user message before this
one and everything after it. Earlier turns are context only. Content between a BEGIN_POWERCONTEXT_<NAME> marker and its
END_POWERCONTEXT_<NAME> marker was supplied by PowerContext from existing memory: it is never evidence for the new turn.

What is worth remembering:
{extraction_rules}

You will work in two steps.

Step 1 (now): list every fact in the evidence that is worth remembering, one entry per fact that can change on its own.
Several facts can share a subject or topic; list each of them. For each fact give a short label naming it (not the
memory text) and a short recall query of a few words naming its subject and topic, used to look up existing memories
on the same fact. List at most {max_items} facts. Step 1 only finds the existing memories to check: include a fact when
in doubt, since step 2 decides what is kept; return an empty list only when the evidence states no fact at all.
Reply with only this JSON object: {"items": [{"fact": "...", "query": "..."}]}

Step 2 (after you receive the existing memories, each labeled M1, M2, ...): return the final memory operations.
Account for every fact you listed in step 1; a decision about one fact never settles another.
- "create": a fact from the evidence that no shown memory states; target_ids empty.
- "revise": the evidence changes or corrects details of exactly one existing memory, such as a cause, a value, a
  status, a date, a name or a path, even when its overall conclusion stays the same; target_ids is that one label, and
  the content is the complete corrected memory.
- "merge": several shown memories state the same fact (with or without the evidence); target_ids lists their labels,
  and the content is the single complete combined memory.
- "noop": an existing memory already states everything the evidence says about the fact; target_ids is that label.
Restating the same fact in other words, another language or with confirming evidence is a noop; a better description
alone is not a reason to revise. When the evidence changes the same enduring fact (same subject, attribute and
applicability) as an existing memory, revise that memory instead of creating another, and keep the earlier value as
history when the change matters. Keep facts that can change independently, and genuinely different conditions,
separate. Do not touch memories unrelated to the facts you listed. Use only the labels shown.

How to write each memory:
- One memory per fact that can change on its own. Do not fold several independent facts into one memory, and do not
  split one fact across memories.
- Write it as completely as a standalone note read without the conversation: keep the exact names, values, dates,
  reasons, conditions and outcomes the evidence gives. Do not shorten a fact to save space.
- Name its subject (the person, project, team or system it is about) in every memory.
- State the resulting fact or state, not the story of how this session reached it.

Every create, revise or merge needs kind (fact, preference, decision, constraint or working_note), text (the complete
atomic fact) and description (a short catalog label). Step 2 replies with only this JSON object:
{"operations": [{"action": "...", "target_ids": [], "kind": "...", "text": "...", "description": "..."}]}
```

Core 实现中，第一段改为描述带 `evidence_id` 的证据输入，第 2 步的输出增加 `evidence_ids` 与 `reason`（§3.2、§3.3）。
